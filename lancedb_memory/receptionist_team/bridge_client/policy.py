"""BridgeRoutePolicy -- which Bridge agent, workspace and operation one request gets.

Nothing here knows any agent's name. The Bridge's own GET /agents answer is the
only list of agents, operations and workspaces; this policy decides, against
that answer, what Toyota is allowed to ask for:

- the operation must be in BridgePolicy.allowed_operations, which can only hold
  read-only operations (inspect, explain, search_memory) -- implement, print,
  scan and anything new the Bridge adds later are refused by default;
- the agent must list that operation and that workspace;
- the workspace must not be one of that agent's write workspaces;
- approval_mode is always read_only.

A request may name an agent (and optionally a workspace and operation):

    hello-world: say hello
    codex@letta_code: where is the dashboard served from?
    codex/explain: what does roster_membership.py do?

An agent named that way must be on the Bridge's list; otherwise the whole text
goes to the configured default agent. A named operation or workspace is never
quietly swapped for a different one: if it isn't allowed, nothing is sent and
the reason is said. That is what "never silently escalate" means here.
"""
import re
from dataclasses import dataclass

from .models import READ_ONLY_OPERATIONS, BridgeAgent, BridgePolicy, BridgeRoute

_ADDRESS = re.compile(
    r"^\s*(?:ask\s+)?(?:the\s+)?(?P<agent>[a-z][a-z0-9-]{0,40})"
    r"(?:@(?P<workspace>[a-z][a-z0-9_-]{0,40}))?"
    r"(?:/(?P<operation>[a-z][a-z_]{0,40}))?\s*:\s*(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)
_LIST = re.compile(
    r"^\s*(?:(?:list|show|discover|which|what)\s+(?:are\s+)?(?:the\s+)?(?:bridge\s+)?agents?"
    r"(?:\s+(?:are\s+)?(?:on|in)\s+the\s+bridge)?|agents)\s*[.?!]?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RouteDecision:
    """Either a route plus the prompt to send, or a refusal to say. Never both."""

    route: BridgeRoute | None
    prompt: str
    refusal: str = ""


def is_listing_request(text: str) -> bool:
    return bool(_LIST.match(text or ""))


def _spoken(names) -> str:
    names = [n.replace("_", " ") for n in names]
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


class BridgeRoutePolicy:
    """Strategy: resolve one request against the live agent list and the policy."""

    def __init__(self, policy: BridgePolicy) -> None:
        self._policy = policy

    @property
    def policy(self) -> BridgePolicy:
        return self._policy

    # -- what can be sent ----------------------------------------------------
    def allowed_operations(self, agent: BridgeAgent) -> list[str]:
        """The agent's operations Toyota may request, in policy order."""
        return [op for op in self._policy.allowed_operations if op in agent.operations]

    def readable_workspaces(self, agent: BridgeAgent) -> list[str]:
        return [ws for ws in agent.workspaces if ws not in agent.write_workspaces]

    def can_use(self, agent: BridgeAgent) -> bool:
        return bool(self.allowed_operations(agent) and self.readable_workspaces(agent))

    # -- resolving one request -------------------------------------------------
    def resolve(self, agents: list[BridgeAgent], text: str) -> RouteDecision:
        listed = {agent.name: agent for agent in agents}
        name, workspace, operation, prompt = self._policy.default_agent, None, None, text.strip()
        address = _ADDRESS.match(text or "")
        if address and address.group("agent").lower() in listed:
            name = address.group("agent").lower()
            workspace = (address.group("workspace") or "").lower() or None
            operation = (address.group("operation") or "").lower() or None
            prompt = address.group("rest").strip()
        elif address and (address.group("workspace") or address.group("operation")):
            # An explicit agent@workspace or agent/operation form for an agent the
            # Bridge doesn't list: never re-aim it at the default agent.
            return self._refuse(f"The Star Trek Bridge doesn't list an agent called {address.group('agent')} "
                                "right now, so I didn't send anything.")
        if not prompt:
            return self._refuse("What would you like me to ask the Bridge?")
        agent = listed.get(name)
        if agent is None:
            return self._refuse(f"The Star Trek Bridge doesn't list {name} right now, so I didn't send anything.")
        chosen_op = self._operation(agent, operation)
        if isinstance(chosen_op, RouteDecision):
            return chosen_op
        chosen_ws = self._workspace(agent, workspace)
        if isinstance(chosen_ws, RouteDecision):
            return chosen_ws
        route = BridgeRoute(agent=agent.name, workspace=chosen_ws, operation=chosen_op,
                            approval_mode=self._policy.approval_mode)
        return RouteDecision(route=route, prompt=prompt)

    def _operation(self, agent: BridgeAgent, requested: str | None):
        if requested is not None:
            if requested not in READ_ONLY_OPERATIONS or requested not in self._policy.allowed_operations:
                return self._refuse(f"I only send read-only requests to the Bridge, and {requested} isn't one, "
                                    "so I didn't send anything.")
            if requested not in agent.operations:
                return self._refuse(f"{agent.name} doesn't offer {requested} on the Bridge, "
                                    "so I didn't send anything.")
            return requested
        allowed = self.allowed_operations(agent)
        if not allowed:
            kind = "isn't a read-only request" if len(agent.operations) == 1 else "aren't read-only requests"
            return self._refuse(f"{agent.name} only does {_spoken(agent.operations)}, which {kind} "
                                "I'm allowed to send, so I didn't send anything.")
        return allowed[0]

    def _workspace(self, agent: BridgeAgent, requested: str | None):
        if requested is not None:
            if requested not in agent.workspaces:
                return self._refuse(f"{agent.name} doesn't work in {requested}, so I didn't send anything.")
            if requested in agent.write_workspaces:
                return self._refuse(f"{agent.name} can write in {requested}, and I only send read-only requests, "
                                    "so I didn't send anything.")
            return requested
        readable = self.readable_workspaces(agent)
        if self._policy.default_workspace in readable:
            return self._policy.default_workspace
        if readable:
            return readable[0]
        return self._refuse(f"{agent.name} has no read-only workspace on the Bridge, so I didn't send anything.")

    @staticmethod
    def _refuse(text: str) -> RouteDecision:
        return RouteDecision(route=None, prompt="", refusal=text)

    # -- saying what is there ------------------------------------------------
    def describe(self, agents: list[BridgeAgent]) -> str:
        if not agents:
            return "The Star Trek Bridge isn't listing any agents right now."
        usable = [a.name for a in agents if self.can_use(a)]
        other = [a.name for a in agents if not self.can_use(a)]
        text = f"The Star Trek Bridge lists {len(agents)} agent{'s' if len(agents) != 1 else ''}: {_spoken([a.name for a in agents])}."
        if usable:
            text += f" I can send read-only requests to {_spoken(usable)}."
        if other:
            text += f" {_spoken(other)} {'does' if len(other) == 1 else 'do'} work I'm not allowed to start from here."
        return text
