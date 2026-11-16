"""What a candidate may change: one named section of the prompt, and not much of it. Pure functions and one policy.

The improver proposes; this decides whether the proposal is allowed, and the improver cannot reach it.
"""
from __future__ import annotations

import hashlib
import re

from .models import PromptPatch


def sha256( text: str ) -> str:
    return hashlib.sha256( text.encode( "utf-8" )).hexdigest()


class SectionChangePolicy:
    """Implements IChangePolicy: a single replacement inside the text between two headings.

    `start` is the section's heading line and `end` the heading that follows it. Everything outside stays
    byte for byte; placeholders keep their counts; no route name appears that was not there before.
    """

    def __init__( self, start: str, end: str, max_growth: int = 900, placeholders: tuple[ str, ... ] = (),
                  route_pattern: str = r"^ROUTE: ([a-z_]+)", forbidden: tuple[ str, ... ] = ()):
        self._start, self._end, self._max_growth = start, end, max_growth
        self._placeholders, self._route, self._forbidden = placeholders, re.compile( route_pattern, re.M ), forbidden

    def _bounds( self, artifact: str ) -> tuple[ int, int ]:
        begin = artifact.find( self._start )
        if begin < 0:
            raise ValueError( f"the artifact has no { self._start !r} section" )
        finish = artifact.find( self._end, begin + len( self._start ))
        return begin, len( artifact ) if finish < 0 else finish

    def permitted_section( self, artifact: str ) -> str:
        begin, finish = self._bounds( artifact )
        return artifact[ begin:finish ]

    def apply( self, artifact: str, patch: PromptPatch ) -> tuple[ str, list[ str ]]:
        problems: list[ str ] = []
        begin, finish = self._bounds( artifact )
        if not patch.find or artifact.count( patch.find ) != 1:
            return artifact, [ f"`find` must occur exactly once in the artifact (found { artifact.count( patch.find ) if patch.find else 0 })" ]
        at = artifact.find( patch.find )
        if at < begin + len( self._start ) or at + len( patch.find ) > finish:
            return artifact, [ "`find` is not inside the permitted section (or it covers the section's heading)" ]
        candidate = artifact[ :at ] + patch.replace + artifact[ at + len( patch.find ): ]
        if candidate == artifact:
            problems.append( "the patch changes nothing" )
        if len( candidate ) - len( artifact ) > self._max_growth:
            problems.append( f"the patch adds { len( candidate ) - len( artifact )} characters; the limit is { self._max_growth }" )
        if self._start in patch.replace or self._end.strip() and self._end.strip() in patch.replace.splitlines():
            problems.append( "the replacement contains a section heading" )
        for token in self._placeholders:
            if candidate.count( token ) != artifact.count( token ):
                problems.append( f"the placeholder { token } was added or removed" )
        added = set( self._route.findall( candidate )) - set( self._route.findall( artifact ))
        if added:
            problems.append( "the patch introduces routes that did not exist: " + ", ".join( sorted( added )))
        for phrase in self._forbidden:
            if phrase.lower() in patch.replace.lower() and phrase.lower() not in patch.find.lower():
                problems.append( f"the replacement contains a forbidden phrase: { phrase !r}" )
        return ( artifact if problems else candidate ), problems
