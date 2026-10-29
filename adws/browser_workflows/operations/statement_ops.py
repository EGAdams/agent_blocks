"""The statement specialist: "get the October 2025 Chase statement" -> the
bank's templated playback, run agent-safe for that month."""
from __future__ import annotations

from typing import Any

from ..interfaces.storage import IPlaybackRepository
from ..interfaces.templating import IPeriodParser, IPeriodVocabulary, IStatementCatalog
from .common import Operation, OperationOutcome, ok, refused

_LIST_WORDS = ( "which banks", "what banks", "which statements", "what statements", "list" )


class StatementRequest( Operation ):
    """No approvals and no secrets, as with agent_request: a login or a
    consequential step blocks and says so. The month is read from the
    request; nothing about the page is guessed."""
    name        = "statement_request"
    description = "Download one month's bank statement a free-text request names (agent-safe)"

    def __init__( self, catalog: IStatementCatalog, parser: IPeriodParser, vocabulary: IPeriodVocabulary,
                  repository: IPlaybackRepository, playback: Operation ) -> None:
        self._catalog    = catalog
        self._parser     = parser
        self._vocabulary = vocabulary
        self._repository = repository
        self._playback   = playback

    def perform( self, args: dict[ str, Any ]) -> OperationOutcome:
        text  = str( args.get( "request" ) or "" ).strip()
        known = ", ".join( s.title for s in self._catalog.sources())
        if any( w in text.casefold() for w in _LIST_WORDS ):
            return ok( f"I can fetch monthly statements from: { known }" )
        source = self._catalog.find( text )
        if source is None:
            return refused( f"which bank? name one of: { known }" )
        period = self._parser.parse( text )
        if period is None:
            return refused( f"which month of { source.title } statement? say e.g. \"October 2025\"" )
        said = f"{ source.title } { source.document } for { self._vocabulary.said( period )}"
        if self._repository.by_name( source.playback ) is None:
            return refused( f"no '{ source.playback }' playback yet: record one month's { source.title } download, "
                            f"then turn it into '{ source.playback }' with adw_browser_parameterize_playback.py" )
        outcome = self._playback.run({ "name": source.playback, "actor": str( args.get( "actor" ) or "agent" ),
                                       "port": args.get( "port" ), "params": self._vocabulary.values( period )})
        if outcome.ok:
            return ok( f"{ said }: { outcome.line }", **outcome.data )
        return refused( f"{ said }: { outcome.line } (if that month's row was not found, it may not be posted yet)",
                        **outcome.data )
