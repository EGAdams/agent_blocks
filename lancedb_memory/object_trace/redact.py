"""BoundedRedactor: what may be written about an input, an output or an error.

Three rules, applied before anything reaches a store:

* a key that names a secret (token, password, cookie, api key, ...) keeps its
  key and loses its value;
* a key that names private text (note, body, prompt, content, ...) becomes
  `{ "sha256": ..., "bytes": n }`, so two records can be compared without
  either holding the words;
* every string is bounded, and any secret-shaped run inside one (a bearer
  token, an `sk-` key, a JWT) is masked even under an innocent key.

Depth, list length and total size are bounded too, so a runaway payload
costs a truncation marker rather than the store.
"""
from __future__ import annotations

import hashlib
import json
import re

SECRET_KEY  = re.compile( r"(token(?!s)|secret|passw|authoriz|api[_-]?key|cookie|credential|private[_-]?key|session[_-]?key)", re.I )
PRIVATE_KEY = re.compile( r"^(note|notes_text|text|body|content|contents|markdown|prompt|message_text|transcript|diff|patch)$", re.I )
SECRET_TEXT = re.compile(
    r"(Bearer\s+[A-Za-z0-9._~+/=-]{8,}"
    r"|\b(sk|gsk|ghp|gho|xox[abprs]|glpat|AKIA)[-_][A-Za-z0-9_-]{12,}"
    r"|\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,})" )
MASK      = "[redacted]"
MAX_DEPTH = 4
MAX_ITEMS = 25
MAX_TOTAL = 6000


def digest( value: str ) -> dict:
    raw = value.encode( "utf-8", errors="replace" )
    return { "sha256": hashlib.sha256( raw ).hexdigest(), "bytes": len( raw )}


class BoundedRedactor:
    def __init__( self, string_limit: int = 300, public_keys: frozenset[ str ] = frozenset()) -> None:
        self._limit  = string_limit
        self._public = public_keys            # keys a caller vouches for, e.g. "summary"

    def text( self, value: str, limit: int = 500 ) -> str:
        cleaned = SECRET_TEXT.sub( MASK, str( value ))
        return cleaned if len( cleaned ) <= limit else cleaned[ :limit ] + f"…(+{ len( cleaned ) - limit })"

    def clean( self, value: object ) -> dict | None:
        if value is None:
            return None
        out = self._walk( value if isinstance( value, dict ) else { "value": value }, 0 )
        size = len( json.dumps( out, default=str ))
        if size > MAX_TOTAL:
            return { "truncated": True, "bytes": size, "keys": sorted( out )[ :MAX_ITEMS ]}
        return out

    def _walk( self, value: object, depth: int ) -> object:
        if isinstance( value, dict ):
            if depth >= MAX_DEPTH:
                return { "truncated": True, "keys": len( value )}
            out: dict = {}
            for i, ( key, item ) in enumerate( value.items()):
                if i >= MAX_ITEMS:
                    out[ "…" ] = f"{ len( value ) - MAX_ITEMS } more keys"
                    break
                out[ str( key )[ :60 ]] = self._field( str( key ), item, depth )
            return out
        if isinstance( value, ( list, tuple, set, frozenset )):
            items = list( value )
            kept  = [ self._walk( v, depth + 1 ) for v in items[ :MAX_ITEMS ]]
            return kept + ([ f"…{ len( items ) - MAX_ITEMS } more" ] if len( items ) > MAX_ITEMS else [])
        if isinstance( value, ( bool, int, float )) or value is None:
            return value
        return self.text( str( value ), self._limit )

    def _field( self, key: str, item: object, depth: int ) -> object:
        if key in self._public:
            return self._walk( item, depth + 1 )
        if SECRET_KEY.search( key ):
            return MASK
        if PRIVATE_KEY.match( key ) and isinstance( item, str ):
            return digest( item )
        return self._walk( item, depth + 1 )
