"""Turning what was done into what can be done again."""
from __future__ import annotations

from typing import Protocol

from browser_workflows.models import Period, RecordedAction

from ..models import CashySession, ProcedureMemory, ResolvedWorkflow, WorkflowVersion


class IWorkflowLearner( Protocol ):
    """A session's trail -> the steps that replay it. No secrets, no values."""

    def steps( self, session: CashySession ) -> list[ RecordedAction ]: ...


class IWorkflowRegistry( Protocol ):
    """Every version of every learned workflow. A new version never replaces
    an old one; retiring is a status, not a delete."""

    def latest( self, bank: str, goal: str, verified_only: bool = True ) -> WorkflowVersion | None: ...

    def versions( self, bank: str = "", goal: str = "" ) -> list[ WorkflowVersion ]: ...

    def add( self, version: WorkflowVersion ) -> WorkflowVersion: ...

    def set_status( self, version_id: int, status: str, run_id: int = 0 ) -> None: ...

    def next_version( self, bank: str, goal: str ) -> int: ...


class IProcedureMemory( Protocol ):
    """Vector memory of what was learned, scoped by bank and goal."""

    @property
    def available( self ) -> bool: ...

    def remember( self, memory: ProcedureMemory ) -> None: ...

    def search( self, text: str, bank: str = "", limit: int = 5 ) -> list[ ProcedureMemory ]: ...


class IWorkflowResolver( Protocol ):
    """Is there already a way to do this? Registry first, then the hand-made ones."""

    def resolve( self, bank: str, goal: str ) -> ResolvedWorkflow | None: ...


class IWorkflowSaver( Protocol ):
    """Learn -> register -> point memory at it. Returns what was saved."""

    def save( self, session: CashySession, period: Period | None ) -> WorkflowVersion: ...
