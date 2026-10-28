"""PostOfficeRoute -- an IRouteOverride: real work for Sonnet or Opus always reaches the post office.

Same lesson as figure_route.py and status_route.py: a route too important to
leave to the model's goodwill. On 2026-10-28 Sydney was asked to "get Sonnet
to replicate the communication system that's in Swarmforge"; the only door
to Sonnet she had was ask_sonnet, a read-only question relay, and its
dispatcher answered that this "doesn't contain an actual question" -- nothing
was ever sent. Now the user's own words decide: if the latest message names a
worker agent *and* asks for work (build, plan, review, fix, replicate ...),
it goes to the post office, which sends it to that agent for real.

The check is deliberately narrow. A plain question to Sonnet ("ask Sonnet
where the router lives") is left to the model, and still reaches ask_sonnet
or the post office as it chooses.
"""
import re

from .latest_user import latest_user_message

_WORKERS = re.compile(r"\b(sonnet|sonet|opus)\b", re.IGNORECASE)
_WORK = re.compile(
    r"\b(build\w*|implement\w*|replicat\w*|make|create|write|fix\w*|refactor\w*|code|program|port|wire|"
    r"plan\w*|design\w*|review\w*|train\w*|deploy\w*|test\w*|add)\b", re.IGNORECASE)


class PostOfficeRoute:
    """Forces `agent_name` (the receptionist's post-office folder) for work addressed to a worker."""

    def __init__(self, agent_name: str) -> None:
        self._agent_name = agent_name

    def forced_agent(self, prompt: str) -> str | None:
        message = latest_user_message(prompt)
        if message and _WORKERS.search(message) and _WORK.search(message):
            return self._agent_name
        return None

    def question(self, prompt: str) -> str:
        return latest_user_message(prompt)
