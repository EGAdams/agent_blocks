"""Interfaces of the improvement engine. Say "interface": each is a `typing.Protocol`.

The roles the task keeps apart are separate interfaces on purpose, and no object
implements two of them:

    worker       the RSI framework's IAgentRunner  (the agent under test, one turn)
    trainer      IEvidenceCollector                (what is known before anything is proposed)
    improver     IImprover                         (writes a patch; never scores one)
    judge        the RSI framework's IEvaluator    (scores a turn; the improver cannot edit it)
    promotion    IPromotionPolicy + the RSI ActivationService and approval gateway
"""
from __future__ import annotations

from typing import Protocol

from .models import ArmScore, CycleRecord, Evidence, PromptPatch, TaskSpec


class IArtifactEnvironment( Protocol ):
    """Where the target's improvable artifact lives, and how the target is made to load it."""

    name: str                                  # "pilot" or "live"
    requires_person: bool                      # True: only a named person may approve a write here

    def read( self ) -> str: ...

    def write( self, body: str ) -> None:
        """Put `body` in place and do whatever makes the target load it. Raises if it cannot."""
        ...

    def loaded( self, expected_body: str ) -> tuple[ str, bool ]:
        """What the target itself loaded: ( its sha256, whether that is `expected_body` ). Raises if unknown."""
        ...

    def describe( self ) -> str: ...


class IEvidenceCollector( Protocol ):
    def collect( self, cycle: CycleRecord ) -> list[ Evidence ]: ...


class IImprover( Protocol ):
    name: str

    def propose( self, artifact: str, permitted: str, failures: list[ ArmScore ],
                 evidence: list[ Evidence ]) -> PromptPatch: ...


class IChangePolicy( Protocol ):
    """What a candidate is allowed to change. Applied by the engine, not by the improver."""

    def permitted_section( self, artifact: str ) -> str: ...

    def apply( self, artifact: str, patch: PromptPatch ) -> tuple[ str, list[ str ]]:
        """The candidate text, and the list of rule violations (empty = allowed)."""
        ...


class IPromotionPolicy( Protocol ):
    """Who may approve an activation in this environment."""

    def may_approve( self, approver: str, environment: str ) -> tuple[ bool, str ]: ...

    def standing_approver( self, environment: str ) -> str:
        """An approver recorded without a person when the gates allow, or "" when a person must decide."""
        ...


class ICycleStore( Protocol ):
    def save( self, cycle: CycleRecord ) -> None: ...

    def load( self, cycle_id: str ) -> CycleRecord: ...

    def all( self ) -> list[ CycleRecord ]: ...

    def by_request_key( self, key: str ) -> CycleRecord | None: ...

    def acquire( self, cycle_id: str, limit: int ) -> bool:
        """Take one of `limit` running slots for this target. False when they are all held by live processes."""
        ...

    def release( self, cycle_id: str ) -> None: ...


class ICancelSignal( Protocol ):
    def requested( self ) -> bool: ...


class IClock( Protocol ):
    def monotonic( self ) -> float: ...

    def sleep( self, seconds: float ) -> None: ...


class ITaskCatalog( Protocol ):
    version: str

    def tasks( self ) -> list[ TaskSpec ]: ...
