"""The rules the two form operations need, with no browser in sight: how a control is named,
which option a request means, and which fields are never typed into or read.

`browser.click` could never work a dropdown, and this is why: Chrome gives an `<option>` a 0x0
box (the open list is drawn by Windows, not by the page), so `RankedElementResolver` rejects
every one of them as zero-size and the answer is always "I could not find one clickable
element". A list is not clicked, it is told its new value -- so the operations resolve the
`<select>` itself, which has a real box, and choose inside it.

A list the site draws itself (role=combobox, a React or jQuery widget) is the opposite case and the
same request: its items are ordinary nodes the page lays out, so they DO have boxes and a click is
the only thing that works on them -- but they are not in the page until the control is opened. That
is `WidgetDropdown`, below, and it is why browser.click on the item alone could never work either way.

A form operation is aimed by `--css` or `--label` only. `--text` is what to type, and a
positional path is the guessing that browser.form_controls exists to end.
"""
from __future__ import annotations

from typing import Any

from browser_workflows.errors import BrowserWorkflowError
from browser_workflows.interfaces.browser import IElementQuery, IWorkflowElement
from browser_workflows.models import LocatorCandidate
from browser_workflows.playback.locating import default_strategy_registry

from .models import FillVerdict, OptionView

#: What a form operation will look at. Buttons are browser.click's job, not these two operations'.
FORM_SCOPE = "input, select, textarea, [role=combobox], [role=textbox], [contenteditable]"

#: Words that make a field one EG's credentials could be in. Refusing a harmless field named
#: "token" costs a sentence; typing into a real one hands a password to a web page.
SECRET_WORDS = ("password", "passwd", "pwd", "passcode", "pin", "otp", "onetime", "one time", "mfa",
                "2fa", "totp", "secret", "token", "apikey", "api key", "cvv", "cvc", "ssn",
                "security code", "security answer")
SECRET_AUTOCOMPLETE = ("current-password", "new-password", "one-time-code", "cc-csc")


def trimmed(text: str | None) -> str:
    """A label as it compares: one space between words, no trailing form punctuation, case-folded.
    "Record Type:" and "Record type *" are both "record type"."""
    return " ".join((text or "").split()).rstrip(":*").strip().casefold()


class FormLabelStrategy:
    """Controls named by the words beside them, as a form writes them.

    This replaces the plain `label` strategy for the form operations only (the registry below
    re-registers it), because that one compares the label exactly: on a real form "Record Type"
    never equals "Record Type:" and the match is lost. Here the colon or required-marker is
    forgiven, and a label that *starts* with the words counts. Two matches are still refused,
    never guessed between -- that is the resolver's job and it stays.
    """

    def matches(self, query: IElementQuery, candidate: LocatorCandidate) -> list[IWorkflowElement]:
        wanted = trimmed(candidate.value)
        if not wanted:
            return []
        found = [e for e in query.find(candidate.tag or FORM_SCOPE)
                 if self._says(e.get_attribute("aria-label"), wanted)]
        for label in query.find("label[for]"):
            if not self._says(label.text, wanted):
                continue
            target = (label.get_attribute("for") or "").replace('"', "")
            if target:
                found += query.find(f'[id="{target}"]')
        return self._once_each(found)

    @staticmethod
    def _says(text: str | None, wanted: str) -> bool:
        said = trimmed(text)
        return bool(said) and (said == wanted or said.startswith(wanted))

    @staticmethod
    def _once_each(found: list[IWorkflowElement]) -> list[IWorkflowElement]:
        """One entry per node: the same control can be reached by its aria-label and by its
        <label>, and two handles on one element would read as two matches (ambiguous)."""
        seen: set[Any] = set()
        unique = []
        for element in found:
            key = element.get_attribute("id") or id(element)
            if key in seen:
                continue
            seen.add(key)
            unique.append(element)
        return unique


def form_strategy_registry():
    """The locator strategies the form operations use: the usual ones, with `label` doing the
    forgiving form match. An extension at the composition point -- browser_workflows' own
    default registry is left exactly as the recorder and playback expect it."""
    registry = default_strategy_registry()
    registry.register("label", FormLabelStrategy)
    return registry


def form_locators_from(args: dict[str, Any]) -> list[LocatorCandidate]:
    """--css (best: paste one from browser.form_controls) or --label. `text` is not a locator here."""
    found = [LocatorCandidate.model_validate(c) for c in args.get("locators") or []]
    if args.get("css"):
        found.append(LocatorCandidate(strategy="css", value=str(args["css"]), source="given", rank=20))
    if args.get("label"):
        found.append(LocatorCandidate(strategy="label", value=str(args["label"]), source="given", rank=30))
    if not found:
        raise BrowserWorkflowError("say which control: --css (from browser.form_controls, best) or --label "
                                   "(the words beside it on the page)")
    return found


class SecretFieldGuard:
    """A field EG's credentials could be in is never filled, read back, or reported.

    The type is the first test (`password`, and `hidden` because that is where a site keeps its
    tokens), then what the site itself says the field is for (autocomplete), then its name and
    label. A false positive refuses an ordinary field and says which word did it; a false
    negative would type into a password box.
    """

    def reason(self, element: IWorkflowElement) -> str:
        tag = (element.tag_name or "").lower()
        kind = (element.get_attribute("type") or "").lower()
        if tag == "input" and kind == "password":
            return "that is a password field, and I never touch those"
        if tag == "input" and kind == "hidden":
            return "that is a hidden field, which is where a site keeps its tokens"
        auto = (element.get_attribute("autocomplete") or "").strip().lower()
        if auto in SECRET_AUTOCOMPLETE:
            return f"the site marks that field {auto!r}, so it is a credential"
        words = self._words(element)
        hit = next((w for w in SECRET_WORDS if w in words), "")
        if hit:
            return f"its name or label says {hit!r}, so it may hold a credential; I never type into those"
        return ""

    @staticmethod
    def _words(element: IWorkflowElement) -> str:
        parts = [element.get_attribute(a) or "" for a in ("id", "name", "aria-label", "placeholder", "title")]
        return " ".join(parts).replace("_", " ").replace("-", " ").replace("$", " ").lower()


class OptionMatcher:
    """Which option a request means.

    `value` is exact or nothing -- it is the form's own token, and a near miss means a different
    record type. `option_text` is what a person reads, so it is matched the way a person would:
    exactly, then ignoring case and the list's punctuation, then as the start of one option, then
    as part of one. Each step must land on exactly one option; two matches are reported with both,
    never guessed between.
    """

    def choose(self, options: list[OptionView], value: str, text: str) -> tuple[OptionView | None, str]:
        if not options:
            return None, "that control has no options, so it is not a dropdown"
        if value:
            hit = [o for o in options if o.value == value]
            if not hit:
                return None, f"no option has the value {value!r}. " + self.listed(options)
            return self._usable(hit[0])
        if not text:
            return None, "say which option: option_text (what it says on the screen) or value. " + self.listed(options)
        wanted = trimmed(text)
        for rule in (lambda o: o.text == text,
                     lambda o: trimmed(o.text) == wanted,
                     lambda o: trimmed(o.text).startswith(wanted),
                     lambda o: wanted in trimmed(o.text)):
            hit = [o for o in options if rule(o)]
            if len(hit) == 1:
                return self._usable(hit[0])
            if len(hit) > 1:
                return None, (f"{text!r} matches {len(hit)} options ("
                              + ", ".join(repr(o.text) for o in hit[:6]) + "); say which, or pass its value")
        return None, f"no option says {text!r}. " + self.listed(options)

    @staticmethod
    def _usable(option: OptionView) -> tuple[OptionView | None, str]:
        if option.disabled:
            return None, f"the option {option.text!r} is disabled, so it cannot be chosen"
        return option, ""

    @staticmethod
    def listed(options: list[OptionView], limit: int = 25) -> str:
        shown = ", ".join(f"{o.text!r} (value {o.value!r})" for o in options[:limit])
        return f"The {len(options)} option(s) are: {shown}" + (", ..." if len(options) > limit else ".")


#: What a site puts its own dropdown's items in once it is open. These are ordinary nodes with real
#: boxes, so they ARE clicked -- the exact opposite of a `<select>`'s `<option>`, which Chrome draws
#: with Windows and gives no box at all.
WIDGET_OPTION_SCOPE = ("[role=option], [role=listbox] li, [role=menuitem], [role=menuitemradio], "
                       "[role=treeitem], li[data-value], .select2-results__option, .chosen-results li")

#: A control the site draws a list for. `role=combobox` is the one the standards ask for; the others are
#: what real pages do instead.
WIDGET_MARKS = ("combobox", "listbox")


def looks_site_drawn(element: IWorkflowElement) -> bool:
    """Is this a dropdown the site draws itself? Asked of anything that is not a `<select>`."""
    role = (element.get_attribute("role") or "").strip().lower()
    popup = (element.get_attribute("aria-haspopup") or "").strip().lower()
    return (role in WIDGET_MARKS or popup in ("listbox", "menu", "tree", "true")
            or element.get_attribute("aria-expanded") is not None
            or element.get_attribute("aria-controls") is not None
            or element.get_attribute("aria-owns") is not None)


class WidgetDropdown:
    """A dropdown the site draws itself: open it, read the items it shows, click the one asked for.

    `<select>` and this are opposites. A native option has no box and must be chosen by value; a
    site's own item is a div or an li the page itself lays out, so it has a box and a click is the
    only thing that works on it. Both are one operation to the caller, which is the point.

    Nothing is guessed at: the items are read *after* the control is opened, so a list that never
    opened reports no items rather than a click into empty space.
    """

    def __init__(self, matcher: OptionMatcher | None = None, scope: str = WIDGET_OPTION_SCOPE) -> None:
        self._matcher = matcher or OptionMatcher()
        self._scope = scope

    def open(self, page: IElementQuery, element: IWorkflowElement) -> list[IWorkflowElement]:
        """Click the control and return the items it is now showing, closest container first."""
        element.click()
        inside = self._owned(page, element)
        return inside if inside else [e for e in page.find(self._scope) if e.is_displayed()]

    def read(self, items: list[IWorkflowElement]) -> list[OptionView]:
        return [OptionView(position=i + 1, text=" ".join((e.text or "").split())[:160],
                           value=(e.get_attribute("data-value") or e.get_attribute("value")
                                  or " ".join((e.text or "").split()))[:160],
                           selected=(e.get_attribute("aria-selected") or "") == "true",
                           disabled=(e.get_attribute("aria-disabled") or "") == "true"
                                    or e.get_attribute("disabled") is not None)
                for i, e in enumerate(items)]

    def choose(self, items: list[IWorkflowElement], options: list[OptionView],
               value: str, text: str) -> tuple[IWorkflowElement | None, OptionView | None, str]:
        option, why = self._matcher.choose(options, value, text)
        if option is None:
            return None, None, why
        return items[option.position - 1], option, ""

    def _owned(self, page: IElementQuery, element: IWorkflowElement) -> list[IWorkflowElement]:
        """The list this control says is its own (aria-controls / aria-owns). Narrower than the whole
        page, which matters on a form carrying several of these."""
        for attribute in ("aria-controls", "aria-owns"):
            named = (element.get_attribute(attribute) or "").split()
            found = [e for one in named if one and '"' not in one
                     for e in page.find(self._inside(one)) if e.is_displayed()]
            if found:
                return found
        return []

    def _inside(self, element_id: str) -> str:
        """The scope again, every clause of it under that one id -- a prefix on the whole string
        would only reach the first clause and quietly search the rest of the page."""
        return ", ".join(f'[id="{element_id}"] {clause.strip()}' for clause in self._scope.split(","))


#: What each HTML field with its own format insists on being given. Typing a date the way a person
#: writes it leaves the field empty, and saying so is more use than reporting a mismatch.
STRICT_FORMATS = {"date": "YYYY-MM-DD", "month": "YYYY-MM", "week": "YYYY-Www", "time": "HH:MM (24-hour)",
                  "datetime-local": "YYYY-MM-DDTHH:MM", "color": "#rrggbb (six hex digits)"}


def same_characters(wanted: str, value: str) -> bool:
    """The same text with the form's own punctuation in it: "(616) 555-1212" is "6165551212" taken
    and reformatted, not a different value. Letters and digits in the same order, nothing dropped."""
    keep = lambda s: "".join(c for c in s.casefold() if c.isalnum())
    return bool(keep(wanted)) and keep(wanted) == keep(value)


class TypedValueCheck:
    """What the field holding something other than what was typed actually means.

    A mismatch is not one thing. A phone field that puts its own brackets in took the text; a field
    with maxlength=10 kept ten characters of it and lost the rest; a date input given 31/12/2025 is
    empty because it only ever accepts YYYY-MM-DD. Each is a different sentence and a different
    cause, and only the first of them is a success.
    """

    def verdict(self, wanted: str, value: str, element: IWorkflowElement) -> FillVerdict:
        if value == wanted:
            return FillVerdict(ok=True)
        if same_characters(wanted, value):
            return FillVerdict(ok=True, note=f"the field reformatted it to {value!r}, which is the same text")
        kind = (element.get_attribute("type") or "").strip().lower()
        wants = STRICT_FORMATS.get(kind)
        if wants:
            held = f"it now holds {value!r}" if value else "it is still empty"
            return FillVerdict(ok=False, cause="wrong_format",
                               note=f"a {kind!r} input only accepts {wants}, so {held}")
        limit = self._limit(element)
        if value and wanted.startswith(value) and limit and len(value) == limit:
            return FillVerdict(ok=False, cause="truncated",
                               note=f"the field accepts at most {limit} characters, so it kept only {value!r}")
        if not value:
            return FillVerdict(ok=False, cause="refused_text",
                               note="the field would not take typed text at all and is still empty")
        return FillVerdict(ok=False, cause="value_differs", note=f"the field ended up holding {value!r}")

    @staticmethod
    def _limit(element: IWorkflowElement) -> int:
        try:
            return int(element.get_attribute("maxlength") or 0)
        except (TypeError, ValueError):
            return 0
