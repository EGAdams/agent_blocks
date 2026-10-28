"""IPlaybackMatcher: which saved playback a request names. Exact name first,
then the one playback whose name's words all appear in the request. Two
candidates is an answer of "which one?", never a pick."""
from __future__ import annotations

import re

from ..models import RecordingSummary

WORD = re.compile( r"[a-z0-9]+" )


def _words( text: str ) -> set[ str ]:
    return set( WORD.findall( text.casefold()))


class WordPlaybackMatcher:
    def match( self, text: str, playbacks: list[ RecordingSummary ]) -> tuple[ RecordingSummary | None, str ]:
        wanted = text.strip().casefold()
        for p in playbacks:
            if p.name.casefold() == wanted:
                return p, ""
        said = _words( text )
        hits = [ p for p in playbacks if _words( p.name ) and _words( p.name ) <= said ]
        if len( hits ) > 1:                       # prefer the most specific name
            best = max( len( _words( h.name )) for h in hits )
            hits = [ h for h in hits if len( _words( h.name )) == best ]
        if len( hits ) == 1:
            return hits[ 0 ], ""
        if hits:
            return None, "more than one playback matches: " + ", ".join( repr( h.name ) for h in hits )
        names = ", ".join( repr( p.name ) for p in playbacks ) or "none saved yet"
        return None, f"no saved playback matches { text.strip()!r}; saved: { names }"
