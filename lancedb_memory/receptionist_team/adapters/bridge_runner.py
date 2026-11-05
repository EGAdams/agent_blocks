"""BridgeDelegationRunner -- Toyota's IDelegationRunner for the Star Trek Bridge.

    Toyota -> RoutingModel (ROUTE: star_trek_bridge | ...) -> this runner
           -> bridge_client.workflow (Discover, Validate, Route, Submit, Monitor, Retrieve, Respond)
           -> bridge_client.client -> HTTPS -> the Bridge -> a Bridge agent

No model decides anything here. The Bridge's own GET /agents answer is the
agent list; bridge_client/policy.py decides, against it, which agent,
workspace and operation a request gets, and only ever a read-only one
(toyota_bridge_route.json can't hold implement, print or scan). The user's
words become the task prompt. A request may name an agent:

    ROUTE: star_trek_bridge | hello-world: say hello
    ROUTE: star_trek_bridge | codex@letta_code: where is the dashboard served from?
    ROUTE: star_trek_bridge | which agents are on the bridge

and otherwise goes to the policy's default agent (codex, read-only inspect).

What it says, by outcome:
- no client credential / bad config: unavailable, nothing was sent;
- an agent, operation or workspace the policy or the Bridge doesn't allow:
  refused, nothing was sent, and never quietly swapped for another;
- submitted and finished inside the short wait: the result, clipped;
- submitted and still queued or running: the task id, never "done";
- failed or cancelled: said as such, with the gateway's sanitized reason;
- network trouble during submit: retried with the SAME idempotency key, and
  if it's still unclear, it says it can't tell whether the task arrived.

"status t_..." / "result t_..." (or plain "status" for her last task) reads a
task back instead of sending a new one; "cancel t_..." asks the Bridge to cancel it.
A task still running when she answers is handed to `notices`, which tells her when it finishes.

bridge_client is imported on first use, the same deferred posture as
PostOfficeRunner, so building a receptionist doesn't open a socket.
"""
import asyncio
import time
import uuid
from collections.abc import Callable

from agent_memory.models import AgentReply

from ..bridge_client.workflow import SPOKEN_LIMIT, SUBMIT_ATTEMPTS  # noqa: F401  (re-exported for tests)


def _default_client():
    from ..bridge_client.client import BridgeClient
    from ..bridge_client.config import load_client_config, load_client_token
    from ..bridge_client.transport import HttpsBridgeTransport
    return BridgeClient(HttpsBridgeTransport(load_client_config(), load_client_token()))


def _default_policy():
    from ..bridge_client.config import load_policy
    return load_policy()


def _default_log():
    from ..bridge_client.workflow import StderrPhaseLog
    return StderrPhaseLog()


class BridgeDelegationRunner:
    """Sends one read-only task to a Bridge agent, lists the agents, or reads a task back."""

    def __init__(self, client_factory: Callable[[], object] = _default_client,
                 policy_factory: Callable[[], object] = _default_policy,
                 wait_seconds: float = 15.0, poll_seconds: float = 5.0,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic,
                 key_factory: Callable[[], str] = lambda: f"toyota-{uuid.uuid4().hex}",
                 log_factory: Callable[[], object] = _default_log,
                 notices: object | None = None) -> None:
        self._notices = notices  # IAlertInbox that reports a task that finished after the answer
        self._client_factory = client_factory
        self._policy_factory = policy_factory
        self._wait = wait_seconds
        self._poll = poll_seconds
        self._sleep = sleep
        self._clock = clock
        self._key_factory = key_factory
        self._log_factory = log_factory
        self.last_task_id: str | None = None
        self.last_trace: list = []

    async def run(self, role: object, agent_name: str, model: str, extra: str = "") -> AgentReply:
        text = (extra or "").strip()
        if not text:
            return AgentReply(text="What would you like me to ask the Star Trek Bridge?")
        return AgentReply(text=await asyncio.to_thread(self._handle, text))

    def _handle(self, text: str) -> str:
        from ..bridge_client.policy import BridgeRoutePolicy
        from ..bridge_client.workflow import BridgeRun, BridgeTaskWorkflow, clip
        try:
            policy = BridgeRoutePolicy(self._policy_factory())
        except (ValueError, OSError) as exc:  # a policy file that is missing or fails validation
            return clip(f"The Star Trek Bridge isn't available from my desk: my route config is invalid "
                        f"({exc.__class__.__name__}). Nothing was sent.")
        workflow = BridgeTaskWorkflow(policy, self._log_factory(), wait_seconds=self._wait,
                                      poll_seconds=self._poll, sleep=self._sleep, clock=self._clock,
                                      key_factory=self._key_factory)
        run = workflow.execute(BridgeRun(text=text, client_factory=self._client_factory,
                                         last_task_id=self.last_task_id))
        self.last_task_id = run.last_task_id
        self.last_trace = run.trace
        if (run.intent == "submit" and run.view is not None and not run.view.terminal
                and run.error is None and self._notices is not None):
            self._notices.track(run.view.task_id, "your question")
        return run.reply
