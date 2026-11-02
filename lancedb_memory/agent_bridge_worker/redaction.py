"""Spot credentials in text the bridge is about to send off this machine. Pure.

A hit never repeats the value: `found()` names the *kind* of secret, and the
caller withholds the whole text rather than trying to cut the secret out.
"""
from __future__ import annotations

import re

PATTERNS: dict[ str, re.Pattern[ str ]] = {
    "private key":     re.compile( r"-----BEGIN [A-Z ]*PRIVATE KEY-----" ),
    "JWT":             re.compile( r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}" ),
    "OpenAI key":      re.compile( r"\bsk-[A-Za-z0-9_-]{20,}" ),
    "Anthropic key":   re.compile( r"\bsk-ant-[A-Za-z0-9_-]{20,}" ),
    "GitHub token":    re.compile( r"\b(?:ghp|gho|ghs|ghu|github_pat)_[A-Za-z0-9_]{20,}" ),
    "Slack token":     re.compile( r"\bxox[abprs]-[A-Za-z0-9-]{10,}" ),
    "AWS key":         re.compile( r"\bAKIA[0-9A-Z]{16}\b" ),
    "Google key":      re.compile( r"\bAIza[0-9A-Za-z_-]{35}" ),
    "assigned secret": re.compile( r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)\b"
                                   r"\s*[:=]\s*['\"]?[^\s'\"]{8,}" ),
}


class SecretScanner:
    """`known` are this process's own secrets (the worker token), matched literally."""

    def __init__( self, known: list[ str ] | None = None ) -> None:
        self._known = [ k for k in ( known or []) if len( k ) >= 12 ]

    def found( self, text: str ) -> list[ str ]:
        kinds = [ name for name, pattern in PATTERNS.items() if pattern.search( text )]
        if any( k in text for k in self._known ):
            kinds.append( "a bridge token" )
        return kinds
