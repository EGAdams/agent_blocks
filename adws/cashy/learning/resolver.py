"""Is there already a way to do this? (IWorkflowResolver, as a Composite.)

`RegistryResolver` answers with Cashy's own newest version that is not
retired; `StatementCatalogResolver` with the hand-made templated playbacks
the statement-specialist uses. `FirstResolver` asks them in order.
"""
from __future__ import annotations

from browser_workflows.interfaces.storage import IRecordingRepository
from browser_workflows.interfaces.templating import IStatementCatalog

from ..interfaces.learning import IWorkflowRegistry, IWorkflowResolver
from ..models import ResolvedWorkflow


class RegistryResolver:
    def __init__( self, registry: IWorkflowRegistry, repository: IRecordingRepository ) -> None:
        self._registry   = registry
        self._repository = repository

    def resolve( self, bank: str, goal: str ) -> ResolvedWorkflow | None:
        version = self._registry.latest( bank, goal, verified_only=False )
        if version is None or self._repository.by_name( version.playback ) is None:
            return None
        return ResolvedWorkflow( playback=version.playback, source="learned", version=version )


class StatementCatalogResolver:
    GOAL = "statement_download"

    def __init__( self, catalog: IStatementCatalog, repository: IRecordingRepository ) -> None:
        self._catalog    = catalog
        self._repository = repository

    def resolve( self, bank: str, goal: str ) -> ResolvedWorkflow | None:
        if goal != self.GOAL:
            return None
        for source in self._catalog.sources():
            if source.key == bank and self._repository.by_name( source.playback ):
                return ResolvedWorkflow( playback=source.playback, source="hand-made" )
        return None


class FirstResolver:
    def __init__( self, resolvers: list[ IWorkflowResolver ]) -> None:
        self._resolvers = resolvers

    def resolve( self, bank: str, goal: str ) -> ResolvedWorkflow | None:
        for resolver in self._resolvers:
            found = resolver.resolve( bank, goal )
            if found:
                return found
        return None
