"""ISensitivityPolicy from the shared rules file the extension also reads."""
from __future__ import annotations

import json
import re
from pathlib import Path

RULES_PATH = Path( __file__ ).resolve().parents[ 1 ] / "chrome_extension" / "sensitivity_rules.json"


class RuleFileSensitivityPolicy:
    """A field is a secret when its type, its autocomplete token, or any of its
    hints (name, id, label, aria label, placeholder) says so. Erring towards
    "secret" costs one question at playback; erring the other way leaks."""

    def __init__( self, path: Path = RULES_PATH ) -> None:
        rules = json.loads( Path( path ).read_text( encoding="utf-8" ))
        self._types    = { t.lower() for t in rules[ "input_types" ]}
        self._tokens   = { t.lower() for t in rules[ "autocomplete_tokens" ]}
        self._prefixes = tuple( p.lower() for p in rules[ "autocomplete_prefixes" ])
        self._pattern  = re.compile( rules[ "hint_pattern" ], re.IGNORECASE )

    def reason( self, input_type: str, hints: dict[ str, str ]) -> str:
        if input_type.lower() in self._types:
            return f"input type '{ input_type.lower() }'"
        for token in ( hints.get( "autocomplete" ) or "" ).lower().split():
            if token in self._tokens or token.startswith( self._prefixes ):
                return f"autocomplete '{ token }'"
        for key in sorted( hints ):
            if key != "autocomplete" and hints[ key ] and self._pattern.search( hints[ key ]):
                return f"{ key } looks sensitive"
        return ""
