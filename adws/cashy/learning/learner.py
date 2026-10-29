"""TrailWorkflowLearner: IWorkflowLearner -- a session's trail as playback steps.

The steps are data the existing playback engine runs: a navigate to where
the trail began, then one click per step that was not undone, each found
by its CSS path first and its words second. A click on the month asked
for is found by its words only, so a templated copy cannot fall back on a
selector that still points at the month it was learned with. No value is
ever written: Cashy never types.
"""
from __future__ import annotations

from urllib.parse import urlparse

from browser_workflows.models import LocatorCandidate, RecordedAction, WaitCondition

from ..interfaces.navigation import IStatementLocator
from ..models import CashySession, NavigationStep


class TrailWorkflowLearner:
    def __init__( self, statements: IStatementLocator ) -> None:
        self._statements = statements

    def steps( self, session: CashySession ) -> list[ RecordedAction ]:
        kept = [ s for s in session.trail if s.action == "click" and not s.undone ]
        if not kept:
            return []
        period_words = [ w.casefold() for w in self._statements.words( session.task.period )] if session.task.period else []
        start = session.trail_start or kept[ 0 ].url_before
        actions = [ RecordedAction( action_type="navigate", url=start,
                                    wait_after=[ WaitCondition( kind="navigation_complete" )])]
        for step in kept:
            names_period = any( w in step.text.casefold() for w in period_words )
            actions.append( RecordedAction( action_type="click", url=step.url_before, locators=_locators( step, names_period ),
                                            metadata={ "text": step.text, "learned_by": "cashy" },
                                            wait_after=_waits( step )))
        return actions


def _locators( step: NavigationStep, words_only: bool ) -> list[ LocatorCandidate ]:
    found = []
    if step.css and not words_only:
        found.append( LocatorCandidate( strategy="css", value=step.css, tag=step.tag, source="css_path", rank=20 ))
    if step.label:
        found.append( LocatorCandidate( strategy="label", value=step.label, tag=step.tag, source="aria_label", rank=30 ))
    if step.text:
        found.append( LocatorCandidate( strategy="text", value=step.text, tag=step.tag, source="text", rank=40 ))
    return found


def _waits( step: NavigationStep ) -> list[ WaitCondition ]:
    before, after = urlparse( step.url_before ).path, urlparse( step.url_after ).path
    if after and after != before:
        return [ WaitCondition( kind="url_contains", value=after )]
    return []
