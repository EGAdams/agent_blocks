"""BridgeTaskRoute -- a question about a Bridge task id is read back from the Bridge, never from memory.

2026-11-05: asked "What is the result of Star Trek Bridge task t_8a71...?" right
after a restart, Toyota's model answered from recalled conversation -- it named a
different task and called it running -- and nothing reached the Bridge. A
readback sends nothing, so it is cheap to force: when the user's own words ask
for a task's status, result or cancel (bridge_client.workflow.readback_of), the
read-only desk answers it from the Bridge. A task id inside a new question
("explain how the gateway assigns ids like t_...") has no opinion here.

Goes right after BridgeConfirmationRoute, ahead of BridgeDeviceRoute.
"""
from .latest_user import latest_user_message
import re

_READ_ONLY_ADDRESS = re.compile(r"^\s*(?:machine-ops@machine_ops|object-debugger@object_trace)(?:/inspect)?\s*:", re.I)


def _claimant(text: str) -> str | None:
    """The specialist whose own ids these are ("status of print job t_..."): it asks again, a readback only repeats."""
    from ..bridge_client.specialists import SPECIALISTS, IClaimsIds
    return next((name for name, spec in SPECIALISTS.items()
                 if isinstance(spec.builder, IClaimsIds) and spec.builder.claims(text)), None)


class BridgeTaskRoute:
    def __init__(self, agent_name: str = "star_trek_bridge") -> None:
        self._agent = agent_name

    def forced_agent(self, prompt: str) -> str | None:
        from ..bridge_client.workflow import readback_of
        text = latest_user_message(prompt)
        if _READ_ONLY_ADDRESS.match(text or ""):
            return self._agent
        found = readback_of(text) if text else None
        if found is None or not found[1]:
            return None
        return _claimant(text) or self._agent

    def question(self, prompt: str) -> str:
        return latest_user_message(prompt)


class BridgeReadOnlyAddressRoute(BridgeTaskRoute):
    """An explicit bridge address wins before generic local inspection keywords."""

    def forced_agent(self, prompt: str) -> str | None:
        return self._agent if _READ_ONLY_ADDRESS.match(latest_user_message(prompt) or "") else None
