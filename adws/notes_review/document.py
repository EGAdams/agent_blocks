"""ReviewDocument: what a note's saved text makes the dashboard show -- its review items, which one blinks.

A pure value object (no I/O). It ports `letta-code/dashboard/js/boot/notes.js` rule for rule, because the page is
the thing EG looks at and the saved text is the thing we can change:

- a **review item** is a line the page paints yellow and that still needs a person: a line holding an unresolved
  `[BRACKET]`, "NOT VERIFIED" or "VERIFY" (`deedReviewKind`, only in the Personal Representative's deed), or a
  line holding a saved `{{highlight:yellow}}...{{/highlight}}` span (any note);
- **focus** is the first `<!-- notes-review-focus: TARGET -->` line (`reviewFocus`). It is metadata: the page
  never renders it. When TARGET is the inner text of a line's first yellow span, that span blinks
  (`hasSelectedYellowHighlight`); when TARGET is a whole paragraph line that is a review item, that line blinks.
  Either way only if exactly one raw line matches (`hasUniqueExplicitFocus`). An empty TARGET means "nothing
  blinks" -- that is how the blink is stopped without deleting a word;
- with no marker at all, the page falls back to its legacy rule (a bracketed "VERIFY WITH ... ATTORNEY" line in
  the "Authority of the Personal Representative" section), ported too, so "what blinks" is never guessed.

An item's id is `r-` + the first ten hex of sha256(its focus target), so it is stable while that item's own words
are unchanged, whatever happens elsewhere in the note. Editing an item's words gives it a new id, and the
operation that did it says so.

Changes are pure functions returning new text. Each changes one line (or the marker line) and nothing else:
`changed_lines` is how a guard proves it.
"""
from __future__ import annotations

import hashlib
import re

from .models import ReviewItem

MARKER_RE = re.compile(r"^\s*<!--\s*notes-review-focus:\s*(.*?)\s*-->\s*$", re.I)
YELLOW_RE = re.compile(r"\{\{highlight:yellow\}\}([\s\S]*?)\{\{/highlight\}\}", re.I)
UNRESOLVED = re.compile(r"\[[^\]\n]*\]|not\s+verified|\bverify\b", re.I)
_PREPARER = re.compile(r"prepared\s+by|preparer|personal representative|\bpr\b")
_LEGAL = re.compile(r"legal description|property description|parcel description|parcel\s+(?:no|number)|tax parcel")
_TAX = re.compile(r"transfer[- ]tax|property[- ]tax|tax exemption|tax-exempt|uncapping")
_BANNER = re.compile(r"items in \[brackets\]", re.I)
_BLANK = re.compile(r"\[_*[A-Z]*_*\]")
_STILL_OPEN = re.compile(r"not\s+verified|\bverify\b|\[[^\]\n]*[A-Za-z][^\]\n]*\]", re.I)
_DEED_TITLE = re.compile(r"personal representative(?:'s)?\s+deed|deed of distribution", re.I)
_HEADING = re.compile(r"^(#{1,4})\s+(.*)$")
_ITEM = re.compile(r"^[-*]\s+(.*)$")
_ATTORNEY = re.compile(r"^\s*\[.*\bVERIFY WITH\b.*\b(?:ATTORNEY|PROBATE COURT)\b.*\]\s*$", re.I)
_AUTHORITY = re.compile(r"authority\s+of\s+the\s+personal\s+representative", re.I)
MAX_TARGET = 4000


class DocumentRefusal(ValueError):
    """A change the page could not show the way the caller means it; the words say why."""


def is_deed(filename: str, source: str) -> bool:
    return "deed" in (filename or "").lower() and bool(_DEED_TITLE.search(source or ""))


def review_kind(line: str) -> str:
    """`deedReviewKind`: which kind of open item a (prefix-stripped) line is, or "" when it needs nobody."""
    if not UNRESOLVED.search(line):
        return ""
    text = line.lower()
    if _PREPARER.search(text):
        return "preparer"
    if _LEGAL.search(text):
        return "legal-description"
    if _TAX.search(text):
        return "tax-language"
    if _BANNER.search(line):
        return ""
    if not _STILL_OPEN.search(_BLANK.sub("", line)):
        return ""
    return "open-item"


def item_id(target: str) -> str:
    return "r-" + hashlib.sha256(target.encode("utf-8")).hexdigest()[:10]


def revision_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _first_yellow(line: str) -> str | None:
    match = YELLOW_RE.search(line)
    return match.group(1) if match else None


def _selected_yellow(line: str, target: str | None) -> bool:
    """`hasSelectedYellowHighlight`, exactly: the first yellow span's inner text, or the whole line, is the target."""
    if not target:
        return False
    inner = _first_yellow(line)
    return inner is not None and (inner == target or line == target)


class ReviewDocument:
    def __init__(self, filename: str, text: str) -> None:
        self.filename = filename
        self.text = text
        self.lines = text.splitlines(keepends=True)
        self.raw = [line.rstrip("\r\n") for line in self.lines]
        self.deed = is_deed(filename, text)
        self.focus, self.explicit = self._focus()
        self.focus_unique = self._unique_focus()
        self.items = self._items()

    @property
    def revision(self) -> str:
        return revision_of(self.text)

    # -- reading ---------------------------------------------------------------------------------

    def _focus(self) -> tuple[str | None, bool]:
        for line in self.raw:
            match = MARKER_RE.match(line)
            if match:
                return match.group(1).strip(), True
        return None, False

    def _unique_focus(self) -> bool:
        if not self.explicit or not self.focus:
            return not self.explicit
        return sum(1 for line in self.raw if line == self.focus or _selected_yellow(line, self.focus)) == 1

    def _rendered(self):
        """(1-based number, raw line, prefix-stripped line, section heading) for each line the page renders."""
        in_mermaid, section = False, ""
        for number, raw in enumerate(self.raw, 1):
            if raw.strip() == "```mermaid":
                in_mermaid = True
                continue
            if in_mermaid:
                in_mermaid = raw.strip() != "```"
                continue
            if MARKER_RE.match(raw) or not raw.strip():
                continue
            heading, listed = _HEADING.match(raw), _ITEM.match(raw)
            if heading:
                section = heading.group(2)
            yield number, raw, heading.group(2) if heading else listed.group(1) if listed else raw, section

    def _blinks(self, line: str, section: str) -> bool:
        """`renderReviewedLine`: does the page give this line (or its first yellow span) the blinking class?"""
        if not self.focus_unique:
            return False
        if self.explicit and _selected_yellow(line, self.focus):
            return True
        kind = review_kind(line) if self.deed else ""
        if not kind:
            return False
        if self.explicit:
            return bool(self.focus) and line == self.focus
        return bool(_AUTHORITY.search(section)) and bool(_ATTORNEY.match(line))

    def _items(self) -> list[ReviewItem]:
        found: list[ReviewItem] = []
        for number, raw, line, section in self._rendered():
            kind = review_kind(line) if self.deed else ""
            inner = _first_yellow(line)
            if not kind and inner is None:
                continue
            target = inner if inner is not None else line
            why = self._why_not(raw, line, target, inner is not None)
            found.append(ReviewItem(
                id=item_id(target), line=number, kind=kind or "highlight", text=line, target=target,
                yellow=inner is not None, open=bool(kind) or bool(inner is not None and review_kind(inner)),
                active=self._blinks(line, section), activatable=not why, why_not=why, section=section))
        return found

    def _why_not(self, raw: str, line: str, target: str, yellow: bool) -> str:
        if not target.strip():
            return "its highlighted text is empty"
        if target != target.strip():
            return "its text starts or ends with spaces, which the page's focus marker cannot hold"
        if "-->" in target or "\n" in target or len(target) > MAX_TARGET:
            return "its text cannot be written into the page's focus marker"
        if not yellow and raw != line:
            return ("it is a heading or a list item, and the page blinks only a paragraph line or a yellow "
                    "highlight; highlight it yellow first")
        count = sum(1 for other in self.raw if other == target or _selected_yellow(other, target))
        if count != 1:
            return f"its text appears on {count} lines, and the page blinks a line only when it is the only one"
        return ""

    def item(self, wanted: str) -> ReviewItem | None:
        return next((i for i in self.items if i.id == wanted), None)

    def active(self) -> ReviewItem | None:
        return next((i for i in self.items if i.active), None)

    def pending(self) -> list[ReviewItem]:
        return [i for i in self.items if i.open or i.yellow]

    def next_after(self, current: ReviewItem | None) -> ReviewItem | None:
        """The next item that can blink, in reading order after `current` (from the top when there is none)."""
        start = current.line if current else 0
        return next((i for i in self.pending() if i.line > start and i.activatable), None)

    # -- changing (each returns new text; nothing is written here) -------------------------------

    def with_focus(self, target: str) -> str:
        """The note with its focus marker naming `target` ("" = nothing blinks). Only the marker line changes;
        a note with no marker gets one appended at the end."""
        if "-->" in target or "\n" in target or target != target.strip():
            raise DocumentRefusal("that text cannot be written into the page's focus marker")
        marker = f"<!-- notes-review-focus: {target} -->" if target else "<!-- notes-review-focus: -->"
        lines = list(self.lines)
        for index, raw in enumerate(self.raw):
            if MARKER_RE.match(raw):
                ending = lines[index][len(raw):]
                lines[index] = marker + ending
                return "".join(lines)
        text = self.text
        if text and not text.endswith(("\n", "\r")):
            text += "\n"
        return text + marker + "\n"

    def with_item_text(self, item: ReviewItem, new_text: str) -> str:
        """The note with one item's words replaced: a yellow item's span keeps its highlight around the new
        words; a plain item keeps its heading or list prefix. Focus that named the item follows it."""
        if "\n" in new_text or "\r" in new_text:
            raise DocumentRefusal("an item is one line; the new text has a line break in it")
        if item.yellow and re.search(r"\{\{/?highlight", new_text, re.I):
            raise DocumentRefusal("the new text holds highlight markup; give the words only")
        raw = self.raw[item.line - 1]
        if item.yellow:
            match = YELLOW_RE.search(raw)
            replaced = raw[:match.start(1)] + new_text + raw[match.end(1):]
        else:
            at = raw.rfind(item.text)
            replaced = raw[:at] + new_text + raw[at + len(item.text):]
        text = self._swap_line(item.line, replaced)
        if self.explicit and self.focus == item.target and new_text.strip():
            text = ReviewDocument(self.filename, text).with_focus(new_text.strip())
        return text

    def without_highlight(self, item: ReviewItem) -> str:
        """The note with the item's yellow span unwrapped (its words kept); focus on it is cleared."""
        raw = self.raw[item.line - 1]
        match = YELLOW_RE.search(raw)
        if not match:
            raise DocumentRefusal("that item has no yellow highlight to remove")
        text = self._swap_line(item.line, raw[:match.start()] + match.group(1) + raw[match.end():])
        if self.explicit and self.focus == item.target:
            text = ReviewDocument(self.filename, text).with_focus("")
        return text

    def _swap_line(self, number: int, new_raw: str) -> str:
        lines = list(self.lines)
        old = lines[number - 1]
        lines[number - 1] = new_raw + old[len(self.raw[number - 1]):]
        return "".join(lines)


def changed_lines(before: str, after: str) -> list[int]:
    """1-based numbers of the lines that differ (a line appended at the end counts as one past the old end)."""
    old, new = before.splitlines(keepends=True), after.splitlines(keepends=True)
    changed = [n for n, (a, b) in enumerate(zip(old, new), 1) if a != b]
    longest = max(len(old), len(new))
    changed += list(range(min(len(old), len(new)) + 1, longest + 1))
    return changed
