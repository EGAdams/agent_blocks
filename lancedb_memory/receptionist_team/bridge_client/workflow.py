"""BridgeTaskWorkflow -- one Bridge request as seven named ADW-style phases.

    Discover -> Validate -> Route -> Submit -> Monitor -> Retrieve -> Respond

Each phase is a small object (IBridgePhase) that reads and writes one shared
BridgeRun and returns a PhaseRecord: its inputs, outputs, status, error and the
phase that comes next. The executor walks the records, so the order lives in
one place and a phase can stop the run early (a refusal, a pending task, an
error) by naming "respond" as its next phase. Respond always runs last and is
the only phase that turns the run into words.

Why not sssf_interfaces' IPhase/IPhaseExecutor: their contract is
execute(run: Run, phase), which needs an SSSF session and a tracer database for
every voice turn -- the same reason agents/cli.py doesn't use them. These
records are the same idea at the weight a receptionist turn can afford, and a
real ADW can wrap this workflow later without it changing.

Every phase record is passed to an IPhaseLog. Records never carry the token or
the prompt text, only ids, names, states and sanitized error codes.
"""
import re
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from . import errors as e
from .models import BridgeResult, BridgeTaskRequest, BridgeTaskView
from .policy import BridgeRoutePolicy, RouteDecision, is_listing_request

SPOKEN_LIMIT = 700
SUBMIT_ATTEMPTS = 3
PhaseStatus = Literal["ok", "stopped", "failed", "skipped"]
PHASES = ("discover", "validate", "route", "submit", "monitor", "retrieve", "respond")

_READBACK = re.compile(r"^\s*(?:what(?:'s| is) the )?(?P<kind>status|result|answer)\b(?:\s+(?:of|for|on))?(?:\s+task)?"
                       r"(?:\s+(?P<id>t_[0-9a-f]{24}))?\s*[.?!]?\s*$", re.IGNORECASE)
# Toyota's model rewords the caller before it reaches this desk ("what is the result of Star Trek
# Bridge task t_..."), so a task id plus a status/result word anywhere is also a readback. A
# readback sends nothing, so leaning this way can never start work by mistake.
_TASK_ID = re.compile(r"\bt_[0-9a-f]{24}\b")
_ASKS_RESULT = re.compile(r"\b(?:result|answer|outcome|reply|said|say|says|finished|done|ready|output)\b", re.IGNORECASE)
_ASKS_STATUS = re.compile(r"\b(?:status|progress|state|queued|running|still)\b", re.IGNORECASE)


def readback_of(text: str) -> tuple[str, str | None] | None:
    """(kind, task id or None) when `text` asks about an earlier Bridge task, else None."""
    exact = _READBACK.match(text)
    if exact:
        return exact.group("kind").lower(), exact.group("id")
    found = _TASK_ID.search(text)
    if not found:
        return None
    if _ASKS_RESULT.search(text):
        return "result", found.group(0)
    if _ASKS_STATUS.search(text):
        return "status", found.group(0)
    return None
PROMPT_FRAME = ("Read-only request relayed by Toyota, the receptionist. Do not modify any file. "
                "Answer briefly, in plain sentences suitable to be read aloud.\n\n")


def clip(text: str) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= SPOKEN_LIMIT else text[:SPOKEN_LIMIT - 1] + "…"


@dataclass(frozen=True)
class PhaseRecord:
    name: str
    status: PhaseStatus
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    next: str = ""

    def line(self) -> str:
        parts = [f"phase={self.name}", f"status={self.status}"]
        parts += [f"{k}={v}" for k, v in {**self.inputs, **self.outputs}.items()]
        if self.error:
            parts.append(f"error={self.error}")
        if self.next:
            parts.append(f"next={self.next}")
        return "[star_trek_bridge] " + " ".join(parts)


class IPhaseLog(Protocol):
    def record(self, record: PhaseRecord) -> None: ...


class StderrPhaseLog:
    """One line per phase on stderr (the sidecar unit appends it to its log)."""

    def record(self, record: PhaseRecord) -> None:
        print(record.line(), file=sys.stderr, flush=True)


class SilentPhaseLog:
    def record(self, record: PhaseRecord) -> None:
        return None


@dataclass
class BridgeRun:
    """The state the phases share for one request."""

    text: str
    client_factory: Callable[[], Any]
    client: Any = None
    last_task_id: str | None = None
    intent: Literal["submit", "readback", "list"] = "submit"
    readback_kind: str = ""
    readback_id: str | None = None  # the task being read back; never changes last_task_id
    agents: list = field(default_factory=list)
    decision: RouteDecision | None = None
    request: BridgeTaskRequest | None = None
    view: BridgeTaskView | None = None
    result: BridgeResult | None = None
    reply: str = ""
    error: e.BridgeError | None = None
    trace: list[PhaseRecord] = field(default_factory=list)


class IBridgePhase(Protocol):
    name: str

    def run(self, ctx: BridgeRun) -> PhaseRecord: ...


# -- the phases ---------------------------------------------------------------
class DiscoverPhase:
    name = "discover"

    def run(self, ctx: BridgeRun) -> PhaseRecord:
        ctx.client = ctx.client or ctx.client_factory()  # no credential -> BridgeUnavailable, nothing sent
        readback = readback_of(ctx.text)
        if readback:
            ctx.intent, ctx.readback_kind = "readback", readback[0]
            ctx.readback_id = readback[1] or ctx.last_task_id
            return PhaseRecord(self.name, "skipped", {"intent": "readback"}, next="monitor")
        ctx.agents = list(ctx.client.discover())
        ctx.intent = "list" if is_listing_request(ctx.text) else "submit"
        return PhaseRecord(self.name, "ok", {"intent": ctx.intent},
                           {"agents": ",".join(a.name for a in ctx.agents)},
                           next="respond" if ctx.intent == "list" else "validate")


class ValidatePhase:
    name = "validate"

    def __init__(self, policy: BridgeRoutePolicy) -> None:
        self._policy = policy

    def run(self, ctx: BridgeRun) -> PhaseRecord:
        ctx.decision = self._policy.resolve(ctx.agents, ctx.text)
        if ctx.decision.route is None:
            ctx.reply = ctx.decision.refusal
            return PhaseRecord(self.name, "stopped", outputs={"allowed": False}, next="respond")
        r = ctx.decision.route
        return PhaseRecord(self.name, "ok", outputs={"allowed": True, "agent": r.agent, "workspace": r.workspace,
                                                     "operation": r.operation, "approval": r.approval_mode},
                           next="route")


class RoutePhase:
    name = "route"

    def __init__(self, key_factory: Callable[[], str]) -> None:
        self._key_factory = key_factory

    def run(self, ctx: BridgeRun) -> PhaseRecord:
        r = ctx.decision.route
        ctx.request = BridgeTaskRequest(agent=r.agent, workspace=r.workspace, operation=r.operation,
                                        prompt=(PROMPT_FRAME + ctx.decision.prompt)[:8000],
                                        approval_mode=r.approval_mode, idempotency_key=self._key_factory())
        return PhaseRecord(self.name, "ok", outputs={"idempotency_key": ctx.request.idempotency_key}, next="submit")


class SubmitPhase:
    name = "submit"

    def __init__(self, sleep: Callable[[float], None], retry_pause: float) -> None:
        self._sleep = sleep
        self._pause = retry_pause

    def run(self, ctx: BridgeRun) -> PhaseRecord:
        for attempt in range(1, SUBMIT_ATTEMPTS + 1):
            try:
                ctx.view = ctx.client.submit(ctx.request)  # same request, same key, every attempt
                break
            except e.BridgeAmbiguous as exc:
                if attempt == SUBMIT_ATTEMPTS:
                    ctx.reply = ("I tried to send that to the Star Trek Bridge but the connection kept failing, "
                                 "and I can't tell whether it arrived. Asking again is safe: it carries the same "
                                 "request key, so it won't run twice.")
                    return PhaseRecord(self.name, "failed", {"attempts": attempt}, error=exc.code, next="respond")
                self._sleep(self._pause)
        ctx.last_task_id = ctx.view.task_id
        return PhaseRecord(self.name, "ok", {"attempts": attempt},
                           {"task_id": ctx.view.task_id, "state": ctx.view.state}, next="monitor")


class MonitorPhase:
    name = "monitor"

    def __init__(self, wait: float, poll: float, sleep: Callable[[float], None], clock: Callable[[], float]) -> None:
        self._wait, self._poll, self._sleep, self._clock = wait, poll, sleep, clock

    def run(self, ctx: BridgeRun) -> PhaseRecord:
        if ctx.intent == "readback":
            if not ctx.readback_id:
                ctx.reply = "Which Bridge task? Give me its id, the one that starts with t_."
                return PhaseRecord(self.name, "stopped", error="no_task_id", next="respond")
            if ctx.readback_kind != "status":
                return PhaseRecord(self.name, "skipped", {"task_id": ctx.readback_id}, next="retrieve")
            ctx.view = ctx.client.status(ctx.readback_id)
            if not ctx.view.terminal:
                ctx.reply = f"Bridge task {ctx.view.task_id} is {ctx.view.state}; it hasn't finished."
                return PhaseRecord(self.name, "stopped", {"task_id": ctx.view.task_id},
                                   {"state": ctx.view.state}, next="respond")
            return PhaseRecord(self.name, "ok", {"task_id": ctx.view.task_id}, {"state": ctx.view.state},
                               next="retrieve")
        deadline = self._clock() + self._wait
        polls = 0
        while not ctx.view.terminal and self._clock() < deadline:
            self._sleep(self._poll)
            ctx.view = ctx.client.status(ctx.view.task_id)
            polls += 1
        if not ctx.view.terminal:
            ctx.reply = (f"I've sent that to the Star Trek Bridge as task {ctx.view.task_id}. It's "
                         f"{ctx.view.state} and not finished yet, so ask me for the status in a bit.")
            return PhaseRecord(self.name, "stopped", {"task_id": ctx.view.task_id},
                               {"state": ctx.view.state, "polls": polls}, next="respond")
        return PhaseRecord(self.name, "ok", {"task_id": ctx.view.task_id},
                           {"state": ctx.view.state, "polls": polls}, next="retrieve")


class RetrievePhase:
    name = "retrieve"

    def run(self, ctx: BridgeRun) -> PhaseRecord:
        task_id = ctx.view.task_id if ctx.view is not None else ctx.readback_id
        ctx.result = ctx.client.result(task_id)
        return PhaseRecord(self.name, "ok", {"task_id": task_id},
                           {"ready": ctx.result.ready, "ok": ctx.result.ok, "state": ctx.result.state},
                           next="respond")


class RespondPhase:
    """The only phase that speaks. Turns a refusal, an error or a result into words."""

    name = "respond"

    def __init__(self, policy: BridgeRoutePolicy) -> None:
        self._policy = policy

    def run(self, ctx: BridgeRun) -> PhaseRecord:
        if ctx.error is not None:
            ctx.reply = self._error(ctx.error)
        elif not ctx.reply and ctx.intent == "list":
            ctx.reply = self._policy.describe(ctx.agents)
        elif not ctx.reply and ctx.result is not None:
            ctx.reply = self._result(ctx.result)
        return PhaseRecord(self.name, "ok", outputs={"chars": len(ctx.reply)})

    @staticmethod
    def _error(exc: e.BridgeError) -> str:
        if isinstance(exc, e.BridgeUnavailable):
            return f"The Star Trek Bridge isn't available from my desk: {exc.message or exc.code}. Nothing was sent."
        if isinstance(exc, e.BridgeAuthError):
            return ("The Star Trek Bridge refused my credential, so nothing ran. "
                    "Someone needs to check Toyota's client token.")
        if isinstance(exc, e.BridgeMalformed):
            return f"The Star Trek Bridge sent back something I couldn't read ({exc.code}), so I can't vouch for any answer."
        if isinstance(exc, e.BridgeRefused):
            return clip(f"The Star Trek Bridge refused that ({exc.code}): {exc.message}")
        return f"I couldn't reach the Star Trek Bridge ({exc.code}). If I'd sent anything, I'd have told you its task id."

    @staticmethod
    def _result(result: BridgeResult) -> str:
        if not result.ready:
            return f"Bridge task {result.task_id} is {result.state}; there's no result yet."
        if result.state == "cancelled":
            return f"Bridge task {result.task_id} was cancelled, so there's no answer from it."
        if not result.succeeded:
            reason = e.sanitize(result.last_error or result.summary or "no reason given")
            return clip(f"Bridge task {result.task_id} failed: {reason}")
        answer = (result.body or result.summary or "").strip() or "it finished but returned no text"
        return clip(f"The Star Trek Bridge says (task {result.task_id}): {answer}")


# -- the executor -------------------------------------------------------------
class BridgeTaskWorkflow:
    """Runs the phases in order, honoring each record's `next`. Never raises a BridgeError."""

    def __init__(self, policy: BridgeRoutePolicy, log: IPhaseLog | None = None,
                 wait_seconds: float = 15.0, poll_seconds: float = 5.0,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                 key_factory: Callable[[], str] = lambda: f"toyota-{uuid.uuid4().hex}") -> None:
        self._log = log or SilentPhaseLog()
        phases: list[IBridgePhase] = [
            DiscoverPhase(), ValidatePhase(policy), RoutePhase(key_factory),
            SubmitPhase(sleep, min(poll_seconds, 2.0)), MonitorPhase(wait_seconds, poll_seconds, sleep, clock),
            RetrievePhase(), RespondPhase(policy),
        ]
        self._phases = {phase.name: phase for phase in phases}
        assert tuple(self._phases) == PHASES

    def execute(self, ctx: BridgeRun) -> BridgeRun:
        name = PHASES[0]
        while name:
            phase = self._phases[name]
            try:
                record = phase.run(ctx)
            except e.BridgeError as exc:
                ctx.error = exc
                record = PhaseRecord(name, "failed", error=e.sanitize(exc.code), next="respond")
            except ValueError as exc:  # a request that fails model validation
                ctx.reply = clip("I won't send that to the Bridge: it doesn't fit my read-only route "
                                 f"({exc.__class__.__name__}).")
                record = PhaseRecord(name, "failed", error=exc.__class__.__name__, next="respond")
            ctx.trace.append(record)
            self._log.record(record)
            name = record.next if name != "respond" else ""
        return ctx
