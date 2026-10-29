"""TokenOptionRanker: IOptionRanker -- which option a few words most likely mean.

Earlier words weigh more (a goal's words are listed best first). A whole
phrase beats scattered tokens, and an exact caption beats both. Links and
buttons only: Cashy never chooses a field to type into.
"""
from __future__ import annotations

import re

from ..models import PageOption, RankedOption

_TOKEN = re.compile( r"[a-z0-9]+" )
_STOP  = frozenset({ "the", "a", "an", "my", "our", "to", "of", "and", "for", "on", "in", "me", "page", "please" })
ACTIONABLE = ( "link", "button", "other" )


def tokens( text: str ) -> set[ str ]:
    return { t for t in _TOKEN.findall( text.casefold()) if t not in _STOP }


class TokenOptionRanker:
    def rank( self, options: list[ PageOption ], words: list[ str ]) -> list[ RankedOption ]:
        ranked = []
        for option in options:
            if option.kind not in ACTIONABLE:
                continue
            best, why = 0.0, ""
            for i, phrase in enumerate( words ):
                weight = 1.0 - 0.05 * i
                score, reason = _score( option, phrase )
                if score * weight > best:
                    best, why = score * weight, reason
            if best > 0:
                ranked.append( RankedOption( option=option, score=round( best, 3 ), why=why ))
        ranked.sort( key=lambda r: ( -r.score, r.option.number ))
        return ranked


def _score( option: PageOption, phrase: str ) -> tuple[ float, str ]:
    said = phrase.casefold().strip()
    for field, scale in (( option.text, 1.0 ), ( option.label, 0.9 )):
        seen = " ".join( field.casefold().split())
        if not seen:
            continue
        if seen == said:
            return 3.0 * scale, f"says exactly '{ phrase }'"
        if re.search( rf"(?<![a-z0-9]){ re.escape( said ) }(?![a-z0-9])", seen ):
            return 2.0 * scale - min( 0.5, len( seen ) / 400 ), f"contains '{ phrase }'"
    wanted = tokens( phrase )
    if not wanted:
        return 0.0, ""
    have = tokens( f"{ option.text } { option.label } { option.href.replace( '-', ' ' ).replace( '/', ' ' )}" )
    overlap = len( wanted & have ) / len( wanted )
    return ( overlap, f"shares { ', '.join( sorted( wanted & have )) }" ) if overlap else ( 0.0, "" )
