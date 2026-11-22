"""One Plan per BATI desktop operation: the model's filled form -> the exact JSON prompt, and the sentence read back.

Strategy + Registry, the same shape as `storage_plans.py`. `BatiDesktopBuilder` (specialists.py) finds the Plan
for `form.operation` in `BATI_PLANS` and asks it to `build()`; adding an operation is one class here and one line
in `BATI_PLANS`. The Plans mirror the worker's table (`agent_bridge_worker/bati_desktop_agent.py`: READS, WRITES,
TAKES, FIELDS), which re-checks everything, and the ADW (`adws/adw_bati_chrome.py`) checks it all a third time --
BATI's own hostname, the dashboard origin only, the `plan-for-today` view only, a visible desktop session.

What code decides, never the model:

- the operation's lane (`inspect` or `implement`), so opening a page can never be sent as a read;
- the machine: always "BATI". This route drives BATI's desktop and nothing else, so there is no machine to choose;
- there is no type, fill, close or quit -- asking for one is answered in words;
- every step that changes the screen is read back and waits for a yes, like every other Bridge change Toyota makes.
"""
from typing import Protocol
from urllib.parse import urlparse

MACHINE = "BATI"
ORIGINS = ("http://localhost:8765", "http://127.0.0.1:8765")
VIEWS = ("plan-for-today",)


class IBatiPlan(Protocol):
    name: str
    operation: str      # the Bridge lane: "inspect" or "implement"

    def build(self, form, problem) -> tuple[dict, str]:
        """(prompt dict, read-back sentence). `problem(spoken)` raises the specialist's FormProblem."""
        ...


def _url(form, problem) -> dict:
    raw = str(form.url or "").strip()
    if not raw:
        return {}
    u = urlparse(raw)
    origin = f"{u.scheme}://{u.hostname}{':' + str(u.port) if u.port else ''}" if u.scheme and u.hostname else ""
    if u.username or u.password or origin not in ORIGINS or len(raw) > 300:
        problem("BATI's Chrome only opens the dashboard (localhost:8765). Should I open that?")
    return {"url": raw}


def _view(form, problem, default: str = "") -> dict:
    raw = str(form.view or "").strip().lower().replace(" ", "-").replace("_", "-") or default
    if not raw:
        return {}
    if raw not in VIEWS:
        problem("The only dashboard view I can show on BATI is the Plan for Today.")
    return {"view": raw}


class _Read:
    operation = "inspect"

    def __init__(self, name: str, takes_view: bool = False) -> None:
        self.name, self._takes_view = name, takes_view

    def build(self, form, problem):
        extra = _view(form, problem) if self._takes_view else {}
        return {"bati": self.name, "machine": MACHINE, **extra}, ""


class Open:
    name, operation = "open", "implement"

    def build(self, form, problem):
        req = {"bati": "open", "machine": MACHINE, **_url(form, problem)}
        return req, "I'll open the dashboard in the debug Chrome on BATI's desktop. It doesn't type anything."


class ShowView:
    name, operation = "show_view", "implement"

    def build(self, form, problem):
        req = {"bati": "show_view", "machine": MACHINE, **_view(form, problem, "plan-for-today")}
        return req, "I'll switch the dashboard on BATI's desktop to the Plan for Today. Nothing is changed in it."


class PlanForToday:
    name, operation = "plan_for_today", "implement"

    def build(self, form, problem):
        req = {"bati": "plan_for_today", "machine": MACHINE, **_url(form, problem)}
        return req, ("I'll open the dashboard on BATI's desktop, show the Plan for Today, check it has today's "
                     "date, and take a screenshot. It doesn't type anything.")


BATI_PLANS: dict[str, IBatiPlan] = {p.name: p for p in (
    _Read("status"), _Read("read", takes_view=True), _Read("screenshot"), Open(), ShowView(), PlanForToday(),
)}
#: Spoken words that are not operations and must never become one. There is no typing and no closing.
NEVER = ("type", "fill", "close", "quit", "click", "login", "log_in", "sign_in", "kill")
