"""owners.json as an IOwnerTable: first matching rule wins."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .models import OwnerRule

OWNERS = Path( __file__ ).resolve().parent / "owners.json"


class JsonOwnerTable:
    def __init__( self, path: Path = OWNERS ) -> None:
        self._rules = [ OwnerRule.model_validate( r ) for r in json.loads( path.read_text())[ "rules" ]]
        self._compiled = [( re.compile( r.agent ), re.compile( r.error ), r ) for r in self._rules ]
        if not self._rules or self._rules[ -1 ].agent != ".*" or self._rules[ -1 ].error != ".*":
            raise ValueError( "owners.json: the last rule must match every failure" )

    def match( self, agent: str, normalised_error: str ) -> OwnerRule:
        for agent_re, error_re, rule in self._compiled:
            if agent_re.search( agent ) and error_re.search( normalised_error ):
                return rule
        return self._rules[ -1 ]

    def rule( self, name: str ) -> OwnerRule | None:
        return next(( r for r in self._rules if r.name == name ), None )
