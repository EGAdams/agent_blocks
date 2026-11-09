"""One Plan per Browser Control operation: the model's filled form -> the exact JSON prompt, and the sentence read back.

Strategy + Registry, the same shape as `notes_plans.py`. `BrowserBuilder` (specialists.py) finds the Plan for
`form.operation` in `BROWSER_PLANS` and asks it to `build()`; adding an operation is one class here and one line in
`BROWSER_PLANS`. The Plans mirror the worker's table (`agent_bridge_worker/browser_agent.py`) and the Browser Control
ADW (`adws/browser_control/`), both of which re-check everything on their side -- this is Toyota's half of the loop
ChatGPT already drives, so she reaches the same operations through the same Bridge rather than a second mechanism.

What code decides, never the model:

- the operation's lane (`inspect` or `implement`), so a change can never be sent as a read;
- the selector a field or dropdown is named by -- it has to come from `form_controls`, and a plan that is handed
  something that doesn't look like a selector asks for a `form_controls` run instead of guessing;
- `confirm` is never set. A click whose words look consequential ("pay", "submit", "delete") is refused by the ADW
  and that refusal is reported, because EG's spoken yes to "shall I send it" is a yes to the request, not standing
  authorization to press a button that moves money. He presses that one himself.

No plan here ever carries a password, a PIN or a one-time code: `fill` refuses a field the ADW marks `secret`, and
this module refuses the text before it is ever sent (see `_credential`).
"""
import re
from typing import Protocol

# The three shapes `form_controls` ever reports (cdp_browser/forms.py `cssFor`), and nothing else: an id, a
# unique attribute match, or the positional path it falls back to. Prose is a legal CSS descendant selector
# ("the project name box" is four tag names), so a selector cannot be told from a sentence by its characters --
# only by its shape. Being strict here costs one `form_controls` run, which is the right answer anyway.
_ID = r"#[\w\\:.-]{1,200}"
_CLASS_CHAIN = r"(?:[a-zA-Z][\w-]*)?(?:\.[\w\\-]{1,100}){1,8}"
_ATTRIBUTE = r"[a-zA-Z][\w-]*\[[\w-]{1,40}=\"[^\"]{1,240}\"\]"
_PATH = r"body(?:\s*>\s*[a-zA-Z][\w-]*(?::nth-of-type\(\d{1,4}\))?){1,30}"
SELECTOR = re.compile(rf"^(?:{_ID}|{_CLASS_CHAIN}|{_ATTRIBUTE}|{_PATH})$")
URL = re.compile(r"^https?://[^\s]{3,2000}$", re.IGNORECASE)
TAB = re.compile(r"^[A-Za-z0-9._:-]{1,120}$")
# What must never be typed into a page from here, whatever the field is called.
# Each suffix is required, not optional. `pass` on its own matches "passing this along", a bare lowercase
# `pin` is "pin the tab", and `token` alone is "tokenomics" -- and a guard that refuses an ordinary sentence
# as a credential teaches the caller to stop believing its refusals, which is the one thing it cannot afford.
_PIN = r"(?-i:\bPIN\b)|\bpin\s*(code|number|#)|\bpin\b(?=\D{0,8}\d)"
CREDENTIAL = re.compile(rf"pass(word|wd|code)s?|{_PIN}|\botp\b|one.?time.?(code|pass)|mfa|2fa|totp|secret|"
                        r"\btokens?\b|api.?key|\bcvv\b|\bcvc\b|\bssn\b|private.?key|"
                        r"BEGIN [A-Z ]*PRIVATE KEY", re.IGNORECASE)
MAX_TEXT = 500
MAX_LIMIT = 200


def words(text: str | None, limit: int = 60) -> str:
    one = " ".join((text or "").split())
    return one if len(one) <= limit else one[: limit - 1] + "…"


class IBrowserPlan(Protocol):
    name: str
    operation: str      # the Bridge lane: "inspect" or "implement"

    def build(self, form, problem) -> tuple[dict, str]:
        """(prompt dict, read-back sentence). `problem(spoken)` raises the specialist's FormProblem."""
        ...


def _tab(form, problem, required: bool = False) -> str:
    tab = str(form.tab or "").strip()
    if not tab:
        if required:
            problem("Which tab? Ask me which tabs are open, then tell me which one.")
        return ""
    if not TAB.fullmatch(tab):
        problem("That doesn't look like a tab id. Ask me which tabs are open and use one of those.")
    return tab


def _locator(form, problem, what: str) -> dict:
    """How one control is named. A selector read off `form_controls` is the right answer; a label is accepted
    because a person speaks labels, but a guessed selector is the commonest reason a form step fails."""
    css = str(form.css or "").strip()
    if css:
        if not SELECTOR.fullmatch(css):
            problem(f"That doesn't look like a selector I can use. Let me list the form's controls first, "
                    f"then I'll {what} the right one.")
        return {"css": css}
    label = str(form.label or "").strip()
    if label:
        return {"label": label[:160]}
    problem(f"Which control should I {what}? Let me list the form's controls first, then say which one.")
    return {}


def _named(locator: dict) -> str:
    for key in ("label", "text"):
        if key in locator:
            return f'the "{words(locator[key])}" control'
    return locator.get("css", "that control")


def _credential(text: str, problem) -> None:
    if CREDENTIAL.search(text):
        problem("That looks like a password or a code, and I never type one into a page. "
                "Type it yourself in the window that's already open.")


class Status:
    name, operation = "status", "inspect"

    def build(self, form, problem):
        return {"browser": "status"}, ""


class Tabs:
    name, operation = "tabs", "inspect"

    def build(self, form, problem):
        return {"browser": "tabs"}, ""


class Read:
    name, operation = "read", "inspect"

    def build(self, form, problem):
        req: dict = {"browser": "read"}
        tab = _tab(form, problem)
        if tab:
            req["tab"] = tab
        return req, ""


class FormControls:
    """The one operation every other form step depends on: what the page offers, each with its selector."""

    name, operation = "form_controls", "inspect"

    def build(self, form, problem):
        req: dict = {"browser": "form_controls"}
        tab = _tab(form, problem)
        if tab:
            req["tab"] = tab
        if form.limit is not None:
            limit = int(form.limit)
            if not 1 <= limit <= MAX_LIMIT:
                problem(f"I can list 1 to {MAX_LIMIT} controls at a time.")
            req["limit"] = limit
        return req, ""


class Open:
    name, operation = "open", "implement"

    def build(self, form, problem):
        url = str(form.url or "").strip()
        if url and not URL.fullmatch(url):
            problem("That doesn't look like a web address. Give me one starting with https://.")
        req: dict = {"browser": "open"}
        if url:
            req["url"] = url
            return req, f"I'll open Chrome on your desktop at {words(url, 120)}."
        return req, "I'll open Chrome on your desktop."


class DashboardNotes:
    name, operation = "dashboard_notes", "implement"

    def build(self, form, problem):
        return ({"browser": "dashboard_notes"},
                "I'll open Chrome on the dashboard and press the NOTES tab, then read the page back to check "
                "NOTES is showing.")


class Navigate:
    name, operation = "navigate", "implement"

    def build(self, form, problem):
        url = str(form.url or "").strip()
        if not URL.fullmatch(url):
            problem("Where should I go? Give me a web address starting with https://.")
        req: dict = {"browser": "navigate", "url": url}
        tab = _tab(form, problem)
        if tab:
            req["tab"] = tab
        return req, f"I'll take that tab to {words(url, 120)}."


class Refresh:
    name, operation = "refresh", "implement"

    def build(self, form, problem):
        req: dict = {"browser": "refresh"}
        tab = _tab(form, problem)
        if tab:
            req["tab"] = tab
        return req, "I'll reload that page."


class SelectTab:
    name, operation = "select_tab", "implement"

    def build(self, form, problem):
        return {"browser": "select_tab", "tab": _tab(form, problem, required=True)}, "I'll bring that tab to the front."


class CloseTab:
    name, operation = "close", "implement"

    def build(self, form, problem):
        tab = _tab(form, problem, required=True)
        return {"browser": "close", "tab": tab}, f"I'll close tab {tab}. Anything typed into it and not saved is lost."


class Click:
    """Press one control the page already offers. `confirm` is never set here -- see this module's docstring."""

    name, operation = "click", "implement"

    def build(self, form, problem):
        locator = _locator(form, problem, "press")
        req: dict = {"browser": "click", **locator}
        tab = _tab(form, problem)
        if tab:
            req["tab"] = tab
        return req, (f"I'll press {_named(locator)} on that page. If it turns out to be something like paying or "
                     "submitting, the workflow will refuse it and you can press it yourself.")


class SelectOption:
    """Choose an item in a dropdown. Works a native <select> and a list the site draws itself; the difference is
    the ADW's business, not the model's -- an item in an open list is never clicked from here."""

    name, operation = "select_option", "implement"

    def build(self, form, problem):
        locator = _locator(form, problem, "set")
        option = str(form.option_text or "").strip()
        value = str(form.value or "").strip()
        if not option and not value:
            problem("Which item should I choose? Tell me what it says in the list.")
        req: dict = {"browser": "select_option", **locator}
        if option:
            req["option_text"] = option[:MAX_TEXT]
            said = f'"{words(option)}"'
        else:
            req["value"] = value[:MAX_TEXT]
            said = f"the item whose value is {words(value)}"
        tab = _tab(form, problem)
        if tab:
            req["tab"] = tab
        return req, f"I'll set {_named(locator)} to {said}, then read it back to check it took."


class Fill:
    """Type into one ordinary field. Never a password field, and never a value this module can see is a credential."""

    name, operation = "fill", "implement"

    def build(self, form, problem):
        locator = _locator(form, problem, "fill in")
        text = form.text if form.text is not None else ""
        if not isinstance(text, str) or len(text) > MAX_TEXT:
            problem(f"That's too much to type into one field; keep it under {MAX_TEXT} characters.")
        _credential(text, problem)
        if form.label:
            _credential(str(form.label), problem)
        req: dict = {"browser": "fill", **locator, "text": text}
        tab = _tab(form, problem)
        if tab:
            req["tab"] = tab
        said = (f"I'll clear {_named(locator)}." if not text.strip()
                else f'I\'ll type "{words(text, 80)}" into {_named(locator)}, then read it back to check it took. '
                     "Nothing is submitted.")
        return req, said


class Download:
    """Press a link that saves a file, and keep the file. Not the same as a click: Chrome writes a download only
    when a folder has been set for the session, so clicking a .tif reported a successful click over an empty
    folder. The workflow reports the name and size of what actually landed, or that nothing did."""

    name, operation = "download", "implement"

    def build(self, form, problem):
        #: Something to press wins over a url: the filler often copies the page's own address beside a link's name.
        url = str(form.url or "").strip()
        if url and not (form.text or form.css or form.label):
            return self._from_url(url, problem)
        #: Unlike the other plans this one accepts `text` as the locator, because what a person says is the
        #: file name, and that is also the link's visible text -- Accela's .tif links carry it nowhere else.
        said_text = str(form.text or "").strip()
        locator = ({"text": said_text[:160]} if said_text and not (form.css or form.label)
                   else _locator(form, problem, "download from"))
        req: dict = {"browser": "download", **locator}
        tab = _tab(form, problem)
        if tab:
            req["tab"] = tab
        return req, (f"I'll press {_named(locator)} and wait for the file to finish saving, then tell you its name "
                     "and size. If nothing arrives, I'll say that rather than call it downloaded.")

    @staticmethod
    def _from_url(url, problem):
        """A bare address: the workflow asks for it in a tab of its own, so no tab is named and nothing is pressed.
        A sign-in page is left open on the desktop for EG, never signed into."""
        if not URL.fullmatch(url):
            problem("That doesn't look like a file's web address. Give me one starting with https://.")
        return {"browser": "download", "url": url}, (
            f"I'll ask for {words(url.split('?')[0], 120)} in a tab of its own in your Chrome and wait for the file to "
            "finish saving. If the site wants you to sign in, I'll leave that page open for you and save nothing.")


BROWSER_PLANS: dict[str, IBrowserPlan] = {p.name: p for p in (
    Status(), Tabs(), Read(), FormControls(),
    Open(), DashboardNotes(), Navigate(), Refresh(), SelectTab(), CloseTab(),
    Click(), SelectOption(), Fill(), Download(),
)}
