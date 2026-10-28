"""TaxProgressPageRoute -- "build us the current tax progress" always builds the page.

The tax progress page is the one place the 2025 tax status is computed from the
live records (adws/adw_tax_progress_page.py in rol_finances), and it lands on
ADW Generated where the family can open it. Left to the model, the request was
answered from the old practice checkpoint (#900001) or from recalled chat; this
makes the route true whether or not the model cooperates, the same argument as
status_route.py and figure_route.py.

It is asked first: "tax progress report" is also, word for word, a status
question and a request for a report, and the page is what was asked for.
"""
import re

from .latest_user import latest_user_message

# "tax" near "progress" or "status", in either order, e.g. "build us the
# current tax progress", "the tax progress page", "where's the tax status page".
_TAX_PROGRESS = re.compile(
    r"\btax(es)?\b.{0,25}\b(progress|status)\b|\b(progress|status)\b.{0,25}\btax(es)?\b",
    re.IGNORECASE | re.DOTALL,
)


class TaxProgressPageRoute:
    """Forces one agent when the latest user message asks for tax progress."""

    def __init__(self, agent_name: str) -> None:
        self._agent_name = agent_name

    def forced_agent(self, prompt: str) -> str | None:
        return self._agent_name if _TAX_PROGRESS.search(self.question(prompt)) else None

    def question(self, prompt: str) -> str:
        return latest_user_message(prompt)
