"""Moving through a bank's site: one press, an exploration, a learned replay."""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from browser_workflows.models import Period

from ..models import ExploreOutcome, PageOption, PlaybackResult, ResolvedWorkflow

if TYPE_CHECKING:
    from ..turn import Turn


class IOptionClicker( Protocol ):
    """Press one option the page offers: weigh its risk, click it, wait for
    the page to settle, record the step. False when it asked instead."""

    def press( self, turn: "Turn", option: PageOption, approved: bool = False ) -> bool: ...


class IExplorer( Protocol ):
    """Mode A: find a goal's page by reading and clicking, asking when unsure."""

    def explore( self, turn: "Turn", goal: str ) -> ExploreOutcome: ...


class IPlaybackRunner( Protocol ):
    """Mode B: run a registered playback, deterministically, through the
    same operation the Smart Menu and the post office use."""

    def run( self, turn: "Turn", workflow: ResolvedWorkflow, period: Period | None ) -> PlaybackResult: ...


class IStatementLocator( Protocol ):
    """The words a statement list uses for one month, best first."""

    def words( self, period: Period ) -> list[ str ]: ...


class IFailureReporter( Protocol ):
    """Write a failure up for the Mechanic: sanitised, expected vs. observed."""

    def report( self, turn: "Turn", expected: str, observed: str ) -> str: ...
