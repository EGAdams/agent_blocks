"""Reduce an error to a stable key. Pure: no I/O beyond reading the rules file once."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

RULES = Path( __file__ ).resolve().parent / "normalise_rules.json"


class RuleFingerprinter:
    """Applies normalise_rules.json in order, then hashes `agent|normalised`."""

    def __init__( self, rules_path: Path = RULES ) -> None:
        data = json.loads( rules_path.read_text())
        self._cuts  = [ str( c ).lower() for c in data.get( "cut_at", [] )]
        self._limit = int( data.get( "max_length", 160 ))
        self._rules = [( re.compile( r[ "pattern" ]), str( r[ "replace" ])) for r in data[ "rules" ]]

    def normalise( self, error: str ) -> str:
        text = ( error or "" ).lower()
        for cut in self._cuts:
            at = text.find( cut )
            if at > 0:
                text = text[ :at ]
        for pattern, replace in self._rules:
            text = pattern.sub( replace, text )
        return text.strip( " .:;|-" )[ :self._limit ].strip()

    def fingerprint( self, agent: str, error: str ) -> str:
        key = f"{ agent }|{ self.normalise( error ) }"
        return hashlib.sha1( key.encode()).hexdigest()[ :12 ]
