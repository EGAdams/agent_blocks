"""Composition root and phases of adw_agent_task.py."""
from __future__ import annotations

import argparse
import asyncio
import shlex
import shutil
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sssf_roles import (IPhase, IPhaseHandle, LifecycleLogged, ManualContextPhaseExecutor, PrintLifecycleLog,
                        SessionRunFactory, SssfConfigLoader)
from adw_modules.data_types import PhaseParams
from agent_comms.executors.claude_cli import ClaudeCliExecutor, ClaudeLoginLifetime, refresh_command
from agent_comms.factory import build_post_office, progress_sink
from agent_comms.live_output import clean, event_name
from agent_comms.models import Envelope, MessageKind, TaskEvent, TaskStatus
from agent_comms.outcome import OUTCOME_RULE, JudgedExecutor
from agent_comms.worker import AgentWorker, NeverRetry
from agent_bridge_worker.redaction import SecretScanner

PROBE, SENDER = "live-probe", "operator"


class Phase(LifecycleLogged, IPhase):
    def __init__(self, name: str, owner: str, description: str, work, logger) -> None:
        LifecycleLogged.__init__(self, logger)
        self._params = PhaseParams(name=name, kind="code", owner=owner, description=description)
        self._work = work

    @property
    def params(self) -> PhaseParams:
        return self._params

    def act(self, handle: IPhaseHandle):
        return self._work(handle)


class Probe:
    """One task: post it, run it while copying its steps into the ADW session, check, report."""

    def __init__(self, kit, executor, workdir: Path, prompt: str, operation: str, checks: list[str],
                 adw_id: str) -> None:
        self._kit, self._workdir, self._checks, self._adw = kit, workdir, checks, adw_id
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.task_id = f"{stamp}_{uuid.uuid4().hex[:8]}_from_{SENDER}"
        text = prompt + ("\n" + OUTCOME_RULE if operation == "implement" else "")
        self._envelope = Envelope(id=self.task_id, kind=MessageKind.REQUEST, sender=SENDER, recipient=PROBE,
                                  task_type=operation, text=text, context={"workdir": str(workdir), "adw_id": adw_id},
                                  correlation_id=self.task_id, trace=[SENDER], idempotent=False)
        self._executor = executor
        self.settled: Envelope | None = None
        self.checks_failed = 0

    def dispatch(self, handle) -> None:
        self._kit.transport.deliver(self._envelope)
        self._publish("task.sent", SENDER, recipient=PROBE, task_type=self._envelope.task_type)
        handle.log(message=f"posted {self.task_id} to {PROBE}")

    def execute(self, handle) -> None:
        worker = AgentWorker(PROBE, self._kit.transport, JudgedExecutor(self._executor), self._kit.events,
                             NeverRetry(), self._kit.liveness)
        done = threading.Event()
        shown = 0

        def work() -> None:
            try:
                self.settled = asyncio.run(worker.work_one(self.task_id))
            finally:
                done.set()

        def copy() -> int:
            events = self._kit.event_log.events_for(self.task_id)
            for e in events[shown:]:
                line = f"{e.details.get('phase', '')}: {e.details.get('message', '')}" if e.name in (
                    "task.progress", "task.output") else " ".join([e.name, *map(str, e.details.values())])
                handle.log(message=line[:700])
                print(f"{e.at.astimezone():%H:%M:%S.%f}"[:-3], PROBE, line[:300], flush=True)
            return len(events)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        while not done.wait(0.5):
            shown = copy()
        shown = copy()
        thread.join()
        if self.settled is None or self.settled.status is not TaskStatus.SUCCEEDED:
            raise RuntimeError(((self.settled and self.settled.error) or "the task did not run").splitlines()[0][:300])

    def verify(self, handle) -> None:
        for command in self._checks:
            self._step(handle, "command", f"$ {command}")
            process = subprocess.Popen(shlex.split(command), cwd=self._workdir, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True)
            for line in process.stdout:
                if line.strip():
                    self._step(handle, "output", line.rstrip())
            code = process.wait()
            self._step(handle, "tool", f"check finished, exit {code}")
            self.checks_failed += code != 0
        verdict = "tested and verified" if self._checks and not self.checks_failed else (
            "check failed" if self.checks_failed else "finished; nothing was independently checked")
        self._step(handle, "finished", verdict)
        if self.checks_failed:
            raise RuntimeError(f"{self.checks_failed} check(s) failed")

    def _step(self, handle, phase: str, message: str) -> None:
        shown = clean(message, SecretScanner().found)
        self._publish(event_name(phase), PROBE, phase=phase, message=shown)
        handle.log(message=f"{phase}: {shown}")
        print(f"{datetime.now():%H:%M:%S.%f}"[:-3], PROBE, f"{phase}: {shown}", flush=True)

    def _publish(self, name: str, agent: str, **details) -> None:
        self._kit.events.publish(TaskEvent(name=name, task_id=self.task_id, agent=agent, details=details))


def run_probe(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="run one real agent task on this machine and watch it")
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--operation", choices=("explain", "implement"), default="explain")
    parser.add_argument("--check", action="append", default=[])
    parser.add_argument("--model", default="haiku")
    parser.add_argument("--config", default="adws/adw_sssf_config/sssf.config.yaml")
    args = parser.parse_args(argv)
    log = PrintLifecycleLog()
    loader = SssfConfigLoader(log)
    cfg = loader.load(args.config)
    loader.validate(cfg, [])
    run = SessionRunFactory(log).create(cfg, None)
    executor = ManualContextPhaseExecutor(log)
    kit = build_post_office()
    binary = shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude")
    login = Path.home() / ".claude" / ".credentials.json"
    claude = ClaudeCliExecutor(args.workdir.resolve(), binary=binary, model=args.model, timeout_seconds=300,
                               max_turns=20, login=ClaudeLoginLifetime(login, refresh_command(binary)),
                               secret_files=[login], progress=progress_sink(kit.events, PROBE))
    probe = Probe(kit, claude, args.workdir.resolve(), args.prompt, args.operation, args.check, run.adw_id)
    print(f"adw_id={run.adw_id} task={probe.task_id}", flush=True)
    problem = ""
    try:
        for name, owner, what, work in (
                ("dispatch_task", SENDER, "Post the task to the probe agent's mailbox", probe.dispatch),
                ("execute_agent", PROBE, "Run the agent, copying each step as it happens", probe.execute),
                ("verify_result", "code", "Run the checks and say what was verified", probe.verify)):
            executor.execute(run, Phase(name, owner, what, work, log))
    except Exception as error:  # noqa: BLE001 -- the run must be closed as failed, with the reason
        problem = str(error)
    print(f"outcome: {'ok' if not problem else problem}", flush=True)
    return run.finish(accepted=not problem, reason=problem or "agent task finished")
