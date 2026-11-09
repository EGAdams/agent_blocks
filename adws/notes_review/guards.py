"""Chain of Responsibility: every write is shown to each guard in turn, and the first objection stops it.

The five rules, each one class, so a rule is enforced in exactly one place and a test names it:

- `RevisionGuard`      -- a content change names the revision the caller read, and the note still holds it;
- `UnrelatedTextGuard` -- only the item's own line (and the focus marker line) may differ afterwards;
- `EvidenceGuard`      -- resolving an item needs evidence or EG's approval, in words, and both are kept;
- `StaysOpenGuard`     -- an edit may not quietly resolve an open item: that is mark_resolved's job;
- `ActuallyResolvedGuard` -- mark_resolved may not claim an item is done while its line still reads as open.
"""
from __future__ import annotations

from .document import changed_lines
from .interfaces import IReviewGuard
from .models import ProposedChange

CONTENT_CHANGES = ("edit", "mark_resolved", "rollback")


class RevisionGuard:
    def objection(self, change: ProposedChange) -> str | None:
        if not change.expected_revision:
            if change.operation in CONTENT_CHANGES:
                return ("name the revision you read (expected_revision, from notes.inspect) -- a change to the "
                        "note's words is never made over a version nobody looked at")
            return None
        if change.expected_revision != change.current_revision:
            return (f"the note changed since revision {change.expected_revision[:12]} was read (it is now "
                    f"{change.current_revision[:12]}); read it again and decide on the newer text")
        return None


class UnrelatedTextGuard:
    def objection(self, change: ProposedChange) -> str | None:
        stray = [n for n in changed_lines(change.before, change.after) if n not in change.allowed_lines]
        if stray:
            return (f"the change would also alter line{'s' if len(stray) > 1 else ''} "
                    f"{', '.join(map(str, stray[:8]))}, which belong to other parts of the note; nothing was written")
        return None


class EvidenceGuard:
    MIN_WORDS = 3

    def objection(self, change: ProposedChange) -> str | None:
        if change.operation != "mark_resolved":
            return None
        grounds = (change.evidence or change.approval or "").strip()
        if len(grounds.split()) < self.MIN_WORDS:
            return ("an item is marked resolved only with evidence (what record shows it, in words) or EG's "
                    "approval (what EG said); neither was given, so the highlight stays")
        return None


class StaysOpenGuard:
    def objection(self, change: ProposedChange) -> str | None:
        if change.operation != "edit" or change.item is None or not change.item.open:
            return None
        if change.new_item is None or not change.new_item.open:
            return ("that edit would resolve an open review item (its line would no longer read as one); "
                    "resolving is mark_resolved, with evidence or EG's approval -- nothing was written")
        return None


class ActuallyResolvedGuard:
    def objection(self, change: ProposedChange) -> str | None:
        if change.operation != "mark_resolved" or change.new_item is None:
            return None
        if change.new_item.open or change.new_item.yellow:
            return ("after that change the line would still read as an open item, so the page would keep "
                    "painting it; give the resolved wording in `replace` -- nothing was written")
        return None


class GuardChain:
    """The chain itself: also an IReviewGuard, so a chain can hold a chain."""

    def __init__(self, guards: list[IReviewGuard]) -> None:
        self._guards = list(guards)

    def objection(self, change: ProposedChange) -> str | None:
        for guard in self._guards:
            found = guard.objection(change)
            if found:
                return found
        return None


def default_chain() -> GuardChain:
    return GuardChain([RevisionGuard(), UnrelatedTextGuard(), EvidenceGuard(), StaysOpenGuard(),
                       ActuallyResolvedGuard()])
