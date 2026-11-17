"""One Plan per Cashy-on-R46 operation: the model's filled form -> the exact JSON prompt, and the sentence read back.

Strategy + Registry, the same shape as `storage_plans.py`. `CashyBuilder` (specialists.py) finds the Plan for
`form.operation` in `CASHY_PLANS` and asks it to `build()`; adding an operation is one class here and one line in
`CASHY_PLANS`. The Plans mirror the worker's table (`agent_bridge_worker/cashy_agent.py`: READS, WRITES, SESSION,
BANK), which re-checks everything, and Cashy herself (`adws/cashy/`) checks it again on R46. Read
`agent_bridge/docs/CASHY_R46.md` first.

What code decides, never the model:

- the operation's lane (`inspect` or `implement`), so a change can never be sent as a read;
- the machine is always R46 -- `cashy-r46` runs nowhere else, and the gateway pins it to R46's worker;
- the session: `r46-bridge` for Fifth Third, `r46-fnbo` for FNBO, so one bank's tab never moves the other's;
- nothing here can approve one of Cashy's consequential steps: the worker sends the Bridge sender as the actor,
  never `user`, and a spoken yes only sends the task. A password, PIN or code is never part of a request;
- `logged_in` is read back as "only after you've signed in yourself", because Cashy acts on that word.
"""
import re
from typing import Protocol

MACHINE = "R46"
# The worker's own patterns (cashy_agent.SESSION / BANK), so a value refused there is refused here first.
SESSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
BANKS = {"fifth_third": "r46-bridge", "fnbo": "r46-fnbo"}
_BANK_WORDS = {"fifth third": "fifth_third", "fifth_third": "fifth_third", "5/3": "fifth_third", "53": "fifth_third",
               "fifth-third": "fifth_third", "fnbo": "fnbo", "first national": "fnbo",
               "first national bank of omaha": "fnbo"}
DEFAULT_SESSION = "r46-bridge"


class ICashyPlan(Protocol):
    name: str
    operation: str      # the Bridge lane: "inspect" or "implement"

    def build(self, form, problem) -> tuple[dict, str]:
        """(prompt dict, read-back sentence). `problem(spoken)` raises the specialist's FormProblem."""
        ...


def _bank(form, problem, required: bool) -> str:
    raw = " ".join(str(form.bank or "").lower().split())
    if not raw:
        if required:
            problem("Which bank should Cashy open on R46: Fifth Third or FNBO?")
        return ""
    bank = _BANK_WORDS.get(raw, raw.replace(" ", "_"))
    if bank not in BANKS:
        problem("Cashy on R46 knows Fifth Third and FNBO. Which one?")
    return bank


def _session(form, problem, bank: str = "") -> str:
    raw = str(form.session or "").strip()
    if not raw:
        return BANKS.get(bank, DEFAULT_SESSION)
    if not SESSION.fullmatch(raw):
        problem("That isn't one of Cashy's sessions. Fifth Third's is r46-bridge and FNBO's is r46-fnbo.")
    return raw


def _base(name: str, session: str) -> dict:
    return {"cashy": name, "machine": MACHINE, "session": session}


class _Read:
    """status / list_options / read_page: what Cashy sees on R46, nothing changed."""

    operation = "inspect"

    def __init__(self, name: str) -> None:
        self.name = name

    def build(self, form, problem):
        return _base(self.name, _session(form, problem, _bank(form, problem, False))), ""


class OpenBank:
    name, operation = "open_bank", "implement"

    def build(self, form, problem):
        bank = _bank(form, problem, True)
        req = {**_base("open_bank", _session(form, problem, bank)), "bank": bank}
        spoken = "Fifth Third" if bank == "fifth_third" else "FNBO"
        return req, (f"I'll have Cashy open {spoken}'s sign-in page on R46, in session {req['session']}. "
                     "She never types a password; whoever is at R46 signs in by hand.")


class LoggedIn:
    name, operation = "logged_in", "implement"

    def build(self, form, problem):
        req = _base("logged_in", _session(form, problem, _bank(form, problem, False)))
        return req, (f"I'll tell Cashy the bank is signed in on R46 (session {req['session']}), and she will start "
                     "reading the account pages. Only say yes if you've signed in there yourself.")


class _Control:
    """pause / resume / stop: Cashy's own run on R46."""

    operation = "implement"
    _WORDS = {"pause": "pause Cashy", "resume": "let Cashy carry on", "stop": "stop Cashy"}

    def __init__(self, name: str) -> None:
        self.name = name

    def build(self, form, problem):
        req = _base(self.name, _session(form, problem, _bank(form, problem, False)))
        return req, f"I'll {self._WORDS[self.name]} on R46, in session {req['session']}."


CASHY_PLANS: dict[str, ICashyPlan] = {p.name: p for p in (
    _Read("status"), _Read("list_options"), _Read("read_page"), OpenBank(), LoggedIn(),
    _Control("pause"), _Control("resume"), _Control("stop"),
)}
#: Spoken words that are not operations and must never become one. Cashy navigates; she never moves money.
NEVER = ("pay", "transfer", "send_money", "move_money", "withdraw", "deposit", "approve", "password", "sign_in")
