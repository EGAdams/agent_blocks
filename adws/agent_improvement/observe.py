"""Decorator over the ADW debugger's event sink: every event gets a millisecond timestamp and is redacted.

The debugger's own `SssfDebugEvents` has no timestamp in the payload and no redaction. This wraps any
`IDebugEvents`, adds `at` (UTC, milliseconds, timezone written out) and the cycle's correlation ids,
and masks anything shaped like a credential before it is written.
"""
from __future__ import annotations

import re
from typing import Any

from .models import now

_PATTERNS = [
    ( re.compile( r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}" ), r"\1 <redacted>" ),
    ( re.compile( r"\b(sk|gsk|xai|pk)[-_][A-Za-z0-9_-]{12,}" ), "<redacted-key>" ),
    ( re.compile( r"(?i)\b(token|api[_-]?key|password|passphrase|secret|cookie)(\s*[=:]\s*)[^\s,;\"']+" ), r"\1\2<redacted>" ),
    ( re.compile( r"\b[0-9a-f]{40,}\b" ), lambda m: m.group( 0 )[ :12 ] + "...<redacted-hex>" ),
]


def redact( value: Any ) -> Any:
    if isinstance( value, str ):
        for pattern, replacement in _PATTERNS:
            value = pattern.sub( replacement, value )
        return value
    if isinstance( value, dict ):
        return { k: redact( v ) for k, v in value.items()}
    if isinstance( value, ( list, tuple )):
        return [ redact( v ) for v in value ]
    return value


class RedactingEvents:
    """Implements IDebugEvents by decorating another one."""

    def __init__( self, inner: Any, correlation: dict[ str, str ]):
        self._inner, self._correlation = inner, dict( correlation )

    def emit( self, kind: str, phase_id: str = "", **fields: Any ) -> None:
        self._inner.emit( kind, phase_id, at=now(), **self._correlation, **redact( fields ))
