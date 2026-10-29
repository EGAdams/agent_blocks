"""ReadOnlyAwareRiskPolicy: IRiskPolicy.

The recorder's keyword classifier flags "Transfer Activity" (it says
"transfer"), and it should when recording. Navigating, a word like
"transfer" or "pay" next to a read-only noun -- activity, history,
statements -- names a page to look at, not an action. The hard words
(submit, confirm, delete, authorize, ...) are never excused.
"""
from __future__ import annotations

import re

from browser_workflows.interfaces.normalizing import IConsequenceClassifier
from browser_workflows.models import LocatorCandidate, RecordedAction

from ..models import PageOption, Risk

#: Words that name something to read about, not something to do.
READ_ONLY = frozenset({ "activity", "history", "statements", "statement", "documents", "details", "summary",
                        "status", "view", "records", "report", "reports", "scheduled", "pending", "overview" })
#: Words no read-only noun excuses.
HARD      = frozenset({ "submit", "delete", "remove", "confirm", "purchase", "buy", "checkout", "authorize",
                        "approve", "withdraw", "close", "cancel", "now", "send" })


class ReadOnlyAwareRiskPolicy:
    def __init__( self, classifier: IConsequenceClassifier ) -> None:
        self._classifier = classifier

    def assess( self, option: PageOption ) -> Risk:
        locators = [ LocatorCandidate( strategy="label", value=option.label, source="aria_label" )] if option.label else []
        reason   = self._classifier.reason( RecordedAction( action_type="click", locators=locators,
                                                            metadata={ "text": option.text }))
        if not reason:
            return Risk( consequential=False )
        words = set( re.findall( r"[a-z]+", f"{ option.text } { option.label }".casefold()))
        if words & READ_ONLY and not words & HARD:
            return Risk( consequential=False, reason=f"{ reason }, but it names a page to read" )
        return Risk( consequential=True, reason=reason )


class NamedDestinationRiskPolicy:
    """Decorator: a plain link whose whole caption is one of the destinations
    the bank profiles name ("Pay & transfer" is how a bank says where
    transfer activity lives) goes somewhere to look, so it is not asked
    about. Only exact captions from that hand-written vocabulary, only links
    with a real address ("#" and javascript: are not), never a hard word: "Pay now" still asks."""

    def __init__( self, inner: "ReadOnlyAwareRiskPolicy", destinations: set[ str ]) -> None:
        self._inner        = inner
        self._destinations = { _caption( d ) for d in destinations }

    def assess( self, option: PageOption ) -> Risk:
        risk = self._inner.assess( option )
        if not risk.consequential or option.kind != "link" or not _address( option.href ):
            return risk
        words = set( re.findall( r"[a-z]+", option.text.casefold()))
        if _caption( option.text ) in self._destinations and not words & HARD:
            return Risk( consequential=False, reason=f"{ risk.reason }, but it is the '{ option.text }' menu" )
        return risk


def _address( href: str ) -> bool:
    """A real place to go -- not "#", not a script pretending to be a link."""
    href = href.strip().casefold()
    return bool( href ) and not href.startswith(( "#", "javascript:" ))


def _caption( text: str ) -> str:
    return " ".join( re.findall( r"[a-z]+", text.casefold().replace( "&", " and " )))
