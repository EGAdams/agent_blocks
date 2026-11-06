"""BridgeSpecialistRunner -- Toyota's IDelegationRunner for one Bridge specialist.

    Toyota (Groq) -> ROUTE: bridge_writer | <the user's words>
      -> this runner -> the specialist's Groq form (bridge_client/specialists.py)
      -> read-back, wait for "yes" (adapters/bridge_confirmation_route.py brings it here)
      -> bridge_client.workflow from Submit (submit, monitor, retrieve, respond) -> Toyota

The result comes back here, in the same turn, when the Bridge finishes inside
the short wait. Otherwise Toyota gives the task id, and the task is handed to
bridge_client.desk.NOTICES, which tells her (and the dashboard) when it finishes.

"status / result / cancel t_..." reads back or cancels any Bridge task, with no
model call. Nothing here raises: every outcome becomes one spoken sentence.
"""
import asyncio
import os
import time
import uuid
from collections.abc import Callable

from agent_memory.models import AgentReply

FORM_MODEL = "openai/gpt-oss-20b"
NOTHING_WAITING = "There's nothing waiting for a yes from me right now, so nothing was sent."


class GroqFormFiller:
    """IFormFiller over Groq's JSON mode. One short call, no tools, no history."""

    def __init__(self, model: str = FORM_MODEL, timeout: float = 10.0) -> None:
        self._model, self._timeout = model, timeout

    def fill(self, system_prompt: str, request: str) -> dict:
        from openai import OpenAI
        client = OpenAI(api_key=os.environ.get("GROQ_API_KEY"), base_url="https://api.groq.com/openai/v1",
                        max_retries=1, timeout=self._timeout)
        reply = client.chat.completions.create(
            model=self._model, response_format={"type": "json_object"}, temperature=0,
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": request}])
        return reply.choices[0].message.content or "{}"


def _default_client():
    from ..bridge_client.desk import _default_client as build
    return build()


class BridgeSpecialistRunner:
    def __init__(self, specialist: str, filler: object | None = None, pending: object | None = None,
                 notices: object | None = None, client_factory: Callable[[], object] = _default_client,
                 wait_seconds: float = 15.0, poll_seconds: float = 5.0,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                 key_factory: Callable[[], str] = lambda: f"toyota-{uuid.uuid4().hex}",
                 log_factory: Callable[[], object] | None = None) -> None:
        from ..bridge_client import desk
        from ..bridge_client.specialists import SPECIALISTS
        self._specialist = SPECIALISTS[specialist]
        self._filler = filler or GroqFormFiller()
        self._pending = pending if pending is not None else desk.PENDING
        self._notices = notices if notices is not None else desk.NOTICES
        self._client_factory = client_factory
        self._wait, self._poll, self._sleep, self._clock = wait_seconds, poll_seconds, sleep, clock
        self._key_factory = key_factory
        self._log_factory = log_factory
        self.last_trace: list = []

    async def run(self, role: object, agent_name: str, model: str, extra: str = "") -> AgentReply:
        text = (extra or "").strip()
        if not text:
            return AgentReply(text=f"What would you like {self._specialist.spoken} to do?")
        system_prompt = getattr(role, "system_prompt", "") or ""
        return AgentReply(text=await asyncio.to_thread(self._handle, text, system_prompt))

    # -- one turn ------------------------------------------------------------
    def _handle(self, text: str, system_prompt: str) -> str:
        from ..bridge_client import errors as e
        from ..bridge_client.desk import yes_or_no
        from ..bridge_client.workflow import RespondPhase, clip, readback_of
        try:
            if readback_of(text):  # before yes/no: "cancel t_..." is not a bare "no"
                return self._workflow().execute(self._run(text)).reply
            answer = yes_or_no(text)
            if answer is not None:
                return self._answer(answer)
            return clip(self._new_request(text, system_prompt))
        except e.BridgeError as exc:
            return RespondPhase.error_sentence(exc)

    def _answer(self, answer: str) -> str:
        pending = self._pending.take()
        if pending is None:
            return NOTHING_WAITING
        if answer == "no":
            return "Okay, I didn't send it. Nothing went to the Star Trek Bridge."
        return self._send(pending.request)

    def _new_request(self, text: str, system_prompt: str) -> str:
        from ..bridge_client.specialists import FormProblem, SpecialistForm
        spec = self._specialist
        client = self._client_factory()
        agent = next((a for a in client.discover() if a.name == spec.bridge_agent), None)
        if agent is None:
            return f"The Star Trek Bridge doesn't list {spec.bridge_agent} right now, so I didn't send anything."
        brief = f"Bridge agent: {agent.name}\n{spec.builder.context(agent)}\n\nRequest: {text}"
        try:
            form = SpecialistForm.parse(self._filler.fill(system_prompt, brief))
            if form.ask:
                return form.ask
            self._attach_revision(form)
            request, readback = spec.builder.build(form, agent)
        except FormProblem as problem:
            return problem.spoken
        except Exception as exc:  # the model call itself: rate limit, timeout, network
            return (f"My Bridge specialist couldn't read that request just now ({type(exc).__name__}), "
                    "so nothing was sent. Try again in a moment.")
        if request.needs_confirmation:
            self._pending.put(spec.name, request, readback)
            return f"{readback} Shall I send it? Say yes or no."
        return self._send(request)

    def _attach_revision(self, form) -> None:
        """An update carries the revision code remembered from the last read, never the model's."""
        from ..bridge_client import desk
        form.expected_revision = None
        if (form.operation or "").strip().lower() == "update" and form.note_id:
            form.expected_revision = desk.NOTE_REVISIONS.get(form.note_id.strip())

    def _remember_revision(self, run) -> None:
        """After a read or update that finished, keep the note's revision for the next update."""
        import json
        from ..bridge_client import desk
        request, result = run.request, run.result
        if request is None or result is None or request.agent != "notes" or not result.succeeded:
            return
        try:
            asked, body = json.loads(request.prompt), json.loads(result.body or "")
            if asked.get("notes") in ("read", "update") and body.get("id") == asked.get("id") \
                    and (asked["notes"] == "read" or body.get("verified")):
                desk.NOTE_REVISIONS.remember(body["id"], body["revision"])
        except (ValueError, TypeError, KeyError, AttributeError):
            return

    def _send(self, request) -> str:
        request = request.model_copy(update={"idempotency_key": self._key_factory()})
        run = self._run("")
        run.request = request
        run = self._workflow().execute(run, start="submit")
        self.last_trace = run.trace
        self._remember_revision(run)
        if run.view is not None and not run.view.terminal and run.error is None:
            self._notices.track(run.view.task_id, self._specialist.spoken.replace("the Bridge's ", ""))
            return run.reply
        return run.reply

    # -- plumbing --------------------------------------------------------------
    def _run(self, text: str):
        from ..bridge_client.workflow import BridgeRun
        return BridgeRun(text=text, client_factory=self._client_factory)

    def _workflow(self):
        from ..bridge_client.config import load_policy
        from ..bridge_client.policy import BridgeRoutePolicy
        from ..bridge_client.workflow import BridgeTaskWorkflow, SilentPhaseLog, StderrPhaseLog
        log = self._log_factory() if self._log_factory else StderrPhaseLog()
        return BridgeTaskWorkflow(BridgeRoutePolicy(load_policy()), log or SilentPhaseLog(),
                                  wait_seconds=self._wait, poll_seconds=self._poll, sleep=self._sleep,
                                  clock=self._clock, key_factory=self._key_factory)
