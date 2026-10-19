"""The debug console's action layer: SSSF phases driven one selection at a time.

This is the integration point, and the only place that knows both halves. It
binds a live `Run` (session + tracer + console) to a `DemoSession` (the file
handle and text), exposes the operations as `ActionSpec` data, and runs exactly
one of them per `invoke()` call.

Why a long-lived Run: a phase is NOT a standalone callable in SSSF. It exists
only as `run.phase(...)`, a context manager on a Run built by `session.ensure`.
So stepping through phases means holding one Run open across menu selections —
the same object an ADW script holds, advanced by a keystroke instead of by the
next line of Python. Nothing in adw_modules changed to allow it.

It returns ActionResult/ActionPreview and prints NOTHING. The terminal menu
renders those objects; an HTTP handler could serialize the same ones.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from . import session
from .data_types import (ActionPreview, ActionRequest, ActionResult, ActionSpec,
                         DemoStateView, PhaseParams, SSSFConfig)
from .debug_steps import DemoSession, StepNotReady

OWNER = "filedemo"              # the code-phase owner, as `map`/`verify`/`git` are

SPECS: list[ActionSpec] = [
    ActionSpec(action_id="open", title="Open file", scope="phase",
               phase_name="open_file", phase_kind="code", owner=OWNER,
               needs_path=True,
               description="Acquire a handle on the chosen file without reading it, "
                           "so the next step has a resource to work from"),
    ActionSpec(action_id="read", title="Read file", scope="phase",
               phase_name="read_file", phase_kind="code", owner=OWNER,
               requires=["open"],
               description="Pull the open file's text into run state and report its "
                           "size, without displaying any of it"),
    ActionSpec(action_id="print", title="Print file contents", scope="phase",
               phase_name="print_file", phase_kind="code", owner=OWNER,
               requires=["read"],
               description="Write the already-read text to the terminal, proving the "
                           "previous phase's state carried forward"),
    ActionSpec(action_id="adw", title="Run the whole demo ADW (3 phases)", scope="adw",
               needs_path=True,
               description="Run open_file, read_file and print_file back to back the "
                           "way an ADW script would, for contrast with stepping"),
    ActionSpec(action_id="state", title="Show current state", scope="inspect",
               description="Print the session id, the demo state and the phases run "
                           "so far, touching nothing"),
    ActionSpec(action_id="logs", title="Show recent logs", scope="inspect",
               description="Tail this session's events.jsonl, the trace every phase "
                           "and console line is written to"),
    ActionSpec(action_id="new_session", title="Start a new session", scope="session",
               description="Finish this adw_id and open a fresh one, carrying the open "
                           "file and its contents over so stepping can continue"),
    ActionSpec(action_id="clear_state", title="Clear this session's state", scope="session",
               description="Release the file handle and forget the contents while "
                           "staying on this adw_id, so one trace shows both attempts"),
    ActionSpec(action_id="reset", title="Reset demonstration (new session + clear)",
               scope="session",
               description="Release the file handle, clear the demo state and finish "
                           "this session so a fresh adw_id starts clean"),
]

BY_ID = {spec.action_id: spec for spec in SPECS}
STEP_IDS = ["open", "read", "print"]


class DebugConsole:
    """One SSSF session, stepped by hand. Construct once per console process."""

    def __init__(self, cfg: SSSFConfig, adw_id: str | None = None):
        self.cfg = cfg
        self.run = session.ensure(cfg, adw_id)
        self.demo = DemoSession()
        self.session_failed = False     # a failed phase finalizes the session

    # ── identity / state ────────────────────────────────────────────────────
    @property
    def adw_id(self) -> str:
        return self.run.adw_id

    @property
    def events_path(self) -> Path:
        return Path(self.cfg.defaults.data_dir) / "sessions" / self.adw_id / "events.jsonl"

    def state(self) -> DemoStateView:
        return self.demo.view(self.adw_id)

    def specs(self) -> list[ActionSpec]:
        return list(SPECS)

    # ── prerequisites: checked BEFORE any phase opens ───────────────────────
    def _unmet(self, spec: ActionSpec) -> list[str]:
        """What is missing, in the engineer's words. Never runs anything itself.

        This is why `not_run` exists as an outcome: the prerequisite check
        happens outside `run.phase`, so a premature selection records no phase
        at all instead of a failure that implies work was attempted.
        """
        missing = []
        if "open" in spec.requires and self.demo.handle is None:
            missing.append("no file is open — choose 'Open file' first")
        if "read" in spec.requires and self.demo.text is None:
            missing.append("nothing has been read — choose 'Read file' first")
        return missing

    def preview(self, request: ActionRequest) -> ActionPreview:
        spec = BY_ID[request.action_id]
        inputs: dict = {}
        if spec.needs_path:
            inputs["path"] = request.path
        if spec.action_id in ("read", "print"):
            inputs["selected_path"] = self.demo.selected_path or "(none)"
        missing = self._unmet(spec)
        return ActionPreview(spec=spec, inputs=inputs,
                             ready=not missing, missing=missing)

    # ── the one entry point a UI needs ──────────────────────────────────────
    def invoke(self, request: ActionRequest) -> ActionResult:
        spec = BY_ID[request.action_id]
        missing = self._unmet(spec)
        if missing:
            return self._not_run(spec, "; ".join(missing))

        if spec.scope == "session":
            return self._session_action(spec)
        if spec.scope == "inspect":
            return self._inspect(spec)
        if spec.scope == "adw":
            return self._whole_adw(spec, request.path)
        return self._one_phase(spec, request.path)

    # ── phase execution ─────────────────────────────────────────────────────
    def _one_phase(self, spec: ActionSpec, path: str) -> ActionResult:
        """Open exactly one SSSF phase and run one step inside it.

        The step's exception is deliberately allowed to reach `run.phase`'s
        except branch: that is what records the phase as `fail` and writes the
        error event. We catch it only on the way out, so the MENU survives
        while the trace still tells the truth.
        """
        clock = time.monotonic()
        detail: list[str] = []
        changed: list[str] = []
        phase_id = ""
        try:
            with self.run.phase(PhaseParams(name=spec.phase_name, kind=spec.phase_kind,
                                            owner=spec.owner,
                                            description=spec.description)) as ph:
                phase_id = ph.phase.phase_id
                payload, detail, changed = self._do_step(spec.action_id, path)
                ph.log(**payload)
        except StepNotReady as error:
            self.session_failed = True
            return self._failed(spec, phase_id, str(error), clock)
        except Exception as error:                      # noqa: BLE001 — menu must live
            self.session_failed = True
            return self._failed(spec, phase_id, f"{type(error).__name__}: {error}", clock)

        self.demo.phases_run.append(spec.phase_name)
        return ActionResult(action_id=spec.action_id, outcome="success",
                            summary=f"phase {spec.phase_name} succeeded",
                            detail=detail, state_changed=changed,
                            elapsed_ms=int((time.monotonic() - clock) * 1000),
                            adw_id=self.adw_id, phase_id=phase_id,
                            log_path=str(self.events_path))

    def _do_step(self, action_id: str, path: str) -> tuple[dict, list[str], list[str]]:
        """The step bodies. Returns (trace payload, display detail, state changed)."""
        if action_id == "open":
            payload = self.demo.open_file(path)
            return (payload,
                    [f"handle open on {payload['path']}",
                     f"{payload['size_on_disk']}B on disk — not read yet"],
                    ["selected_path", "handle_open", "contents cleared"])
        if action_id == "read":
            payload = self.demo.read_file()
            return (payload,
                    [f"{payload['bytes']}B, {payload['lines']} lines, "
                     f"{payload['characters']} characters held in state",
                     "contents not displayed — that is the next phase"],
                    ["text", "byte_count", "line_count", "read_at"])
        if action_id == "print":
            payload = self.demo.contents_to_print()
            head = [f"--- contents read at {payload['read_at']} "
                    f"({payload['shown']} of {payload['total']} lines) ---"]
            tail = ([] if payload["shown"] == payload["total"]
                    else [f"--- {payload['total'] - payload['shown']} more lines not shown ---"])
            return ({"lines_printed": payload["shown"], "lines_total": payload["total"]},
                    head + payload["lines"] + tail,
                    [])                     # printing changes nothing
        raise StepNotReady(f"unknown step {action_id!r}")

    def _whole_adw(self, spec: ActionSpec, path: str) -> ActionResult:
        """Three phases, one selection — what an ADW script does on its own."""
        clock = time.monotonic()
        self.demo.release()
        self.demo.forget_contents()
        detail = ["running 3 phases: open_file -> read_file -> print_file"]
        last = ""
        for step_id in STEP_IDS:
            result = self._one_phase(BY_ID[step_id], path)
            detail.append(f"[{BY_ID[step_id].phase_name}] {result.outcome}: {result.summary}")
            detail.extend(f"    {line}" for line in result.detail)
            last = result.phase_id
            if result.outcome != "success":
                return ActionResult(action_id=spec.action_id, outcome="fail",
                                    summary=f"ADW stopped at {BY_ID[step_id].phase_name}",
                                    detail=detail, error=result.error,
                                    elapsed_ms=int((time.monotonic() - clock) * 1000),
                                    adw_id=self.adw_id, phase_id=last,
                                    log_path=str(self.events_path))
        return ActionResult(action_id=spec.action_id, outcome="success",
                            summary="all 3 phases succeeded", detail=detail,
                            state_changed=["selected_path", "text", "phases_run"],
                            elapsed_ms=int((time.monotonic() - clock) * 1000),
                            adw_id=self.adw_id, phase_id=last,
                            log_path=str(self.events_path))

    # ── inspection (no phase: looking is not work the trace should record) ──
    def _inspect(self, spec: ActionSpec) -> ActionResult:
        clock = time.monotonic()
        if spec.action_id == "state":
            detail = self._state_lines()
            changed: list[str] = []
        else:
            detail = self._log_lines()
            changed = []
        return ActionResult(action_id=spec.action_id, outcome="success",
                            summary=spec.title, detail=detail, state_changed=changed,
                            elapsed_ms=int((time.monotonic() - clock) * 1000),
                            adw_id=self.adw_id, log_path=str(self.events_path))

    # ── session lifecycle ───────────────────────────────────────────────────
    def _session_action(self, spec: ActionSpec) -> ActionResult:
        """New session / clear state / both. No phase: this is not the work.

        The three differ on exactly two axes, which is why they are three items
        and not one:

            action        adw_id      demo state
            new_session   fresh       carried over
            clear_state   unchanged   released and cleared
            reset         fresh       released and cleared
        """
        clock = time.monotonic()
        if spec.action_id == "clear_state":
            released = self.demo.clear()
            detail = ["file handle closed" if released else "no file handle was open",
                      "selection and contents cleared",
                      f"still session {self.adw_id} — the phases already traced stay"]
            changed = ["handle_open", "selected_path", "text"]
        else:
            carried = self.demo.selected_path if spec.action_id == "new_session" else ""
            if spec.action_id == "reset":
                self.demo.clear()
            closed = self._start_new_session()
            detail = [f"session {closed} finished",
                      f"new adw_id {self.adw_id} — phase numbering restarts at 01"]
            detail.append(f"carried over, still open: {carried}" if carried
                          else "no file state carried over")
            changed = ["adw_id", "phases_run"]
        return ActionResult(action_id=spec.action_id, outcome="success",
                            summary=spec.title, detail=detail, state_changed=changed,
                            elapsed_ms=int((time.monotonic() - clock) * 1000),
                            adw_id=self.adw_id, log_path=str(self.events_path))

    def _start_new_session(self) -> str:
        """Settle this run, join a fresh one. Returns the adw_id it closed.

        `Run.finish` is documented "call this exactly once", so a brand new Run
        from `session.ensure` is the only honest way to keep stepping after a
        session is closed — the same call an ADW process makes at startup.
        `phases_run` is emptied because it describes one session's trace, while
        the file handle is a process resource and deliberately outlives it.
        """
        closed = self.adw_id
        self.run.finish(accepted=not self.session_failed,
                        reason="a phase failed during this session" if self.session_failed
                               else "closed from the console menu")
        self.run = session.ensure(self.cfg, None)
        self.session_failed = False
        self.demo.phases_run.clear()
        return closed

    def _state_lines(self) -> list[str]:
        view = self.state()
        lines = [f"adw_id            : {view.adw_id}",
                 f"engineer          : {self.run.engineer}",
                 f"selected_path     : {view.selected_path or '(none)'}",
                 f"handle_open       : {view.handle_open}",
                 f"opened_at         : {view.opened_at or '(never)'}",
                 f"contents in state : {view.has_text}",
                 f"read_at           : {view.read_at or '(never)'}",
                 f"bytes / lines     : {view.byte_count} / {view.line_count}",
                 f"phases run        : {', '.join(view.phases_run) or '(none)'}",
                 f"trace db          : {self.cfg.observability.db}",
                 f"events            : {self.events_path}"]
        if self.session_failed:
            lines.append("session           : FAILED — a phase failed, so SSSF has "
                         "finalized this run. Reset for a clean adw_id.")
        return lines

    def _log_lines(self, limit: int = 15) -> list[str]:
        """Tail SSSF's own per-session trace. Read-only; no new facility added."""
        path = self.events_path
        if not path.exists():
            return [f"no events yet at {path}"]
        rows = path.read_text().splitlines()[-limit:]
        out = [f"last {len(rows)} events from {path}"]
        for row in rows:
            try:
                event = json.loads(row)
            except json.JSONDecodeError:
                continue
            payload = event.get("payload") or {}
            note = payload.get("message") or payload.get("error") or payload.get("status") or ""
            out.append(f"  {event.get('ts', '')[11:23]}  {event.get('type', ''):<12}"
                       f"  {event.get('name', ''):<12}  {str(note)[:70]}")
        return out

    # ── result constructors ─────────────────────────────────────────────────
    def _not_run(self, spec: ActionSpec, why: str) -> ActionResult:
        return ActionResult(action_id=spec.action_id, outcome="not_run",
                            summary="prerequisite missing — nothing ran, no phase opened",
                            detail=[why], adw_id=self.adw_id,
                            log_path=str(self.events_path))

    def _failed(self, spec: ActionSpec, phase_id: str, error: str,
                clock: float) -> ActionResult:
        return ActionResult(action_id=spec.action_id, outcome="fail",
                            summary=f"phase {spec.phase_name} failed",
                            detail=[], error=error,
                            elapsed_ms=int((time.monotonic() - clock) * 1000),
                            adw_id=self.adw_id, phase_id=phase_id,
                            log_path=str(self.events_path))

    # ── shutdown ────────────────────────────────────────────────────────────
    def close(self) -> int:
        """Release the handle and settle the run. Exit's only job."""
        self.demo.release()
        if not self.run.phases:
            # A menu that only launched a separate ADW did no file-demo work.
            # Closing that idle console is not a failed workflow attempt.
            self.run.tracer.session_finish(self.adw_id, ok=True)
            return 0
        return self.run.finish(accepted=not self.session_failed,
                               reason="a phase failed during this console session"
                                      if self.session_failed else "")
