"""MaskingSanitizer: ISessionSanitizer, and the masking every store uses.

Account-like digit runs (five or more digits, spaces and dashes allowed)
become "•" + their last four; money becomes "$…"; URLs lose their query
string and fragment, where banks put tokens. Four-digit runs stay: they
are years far more often than accounts, and a last-four is already masked.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse

from ..models import CashySession, NavigationStep, PageOption

_ACCOUNT = re.compile( r"(?<![\d•])\d(?:[ -]?\d){4,}(?!\d)" )
_MONEY   = re.compile( r"[-+]?\$\s?\d[\d,]*(?:\.\d{2})?" )


def mask_text( text: str ) -> str:
    text = _MONEY.sub( "$…", text )
    return _ACCOUNT.sub( lambda m: "•" + re.sub( r"\D", "", m.group( 0 ))[ -4: ], text )


def mask_url( url: str ) -> str:
    if not url:
        return ""
    parts = urlparse( url )                     # the host (and its port) is never an account
    return urlunparse(( parts.scheme, parts.netloc, mask_text( parts.path ), "", "", "" ))


class MaskingSanitizer:
    def clean( self, session: CashySession ) -> CashySession:
        return session.model_copy( deep=True, update={
            "url":         mask_url( session.url ),
            "trail_start": mask_url( session.trail_start ),
            "options":     [ _option( o ) for o in session.options ],
            "trail":       [ _step( s ) for s in session.trail ],
            "question":    session.question.model_copy( update={
                               "choices": [ _option( o ) for o in session.question.choices ],
                               "hints":   [ h.model_copy( update={ "last_amount": mask_text( h.last_amount ),
                                                                   "payee": mask_text( h.payee )})
                                            for h in session.question.hints ]})
                           if session.question else None,
        })


def _option( o: PageOption ) -> PageOption:
    return o.model_copy( update={ "text": mask_text( o.text ), "label": mask_text( o.label ),
                                  "href": mask_url( o.href ), "css": mask_text( o.css )})


def _step( s: NavigationStep ) -> NavigationStep:
    return s.model_copy( update={ "text": mask_text( s.text ), "label": mask_text( s.label ), "css": mask_text( s.css ),
                                  "url_before": mask_url( s.url_before ), "url_after": mask_url( s.url_after )})
