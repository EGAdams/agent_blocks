"""Liz's desk: what a sentence to Liz asks for, read without a model.

`read_request` maps the things EG says ("what changed between the 2024 and
2025 forms?", "I've finished scanning it", "the payment was $400", "resume
the tax preparation") to one `DeskRequest`. The order of the rules is the
behaviour: an answer ("there weren't any payments") is checked before a
question about payments, and "resume" before "status". Anything that
matches nothing is `UNKNOWN`, which the receptionist hands to the model
rather than guessing an action.
"""
from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, ConfigDict


class Intent( str, Enum ):
    PREPARE      = "prepare"         # start / run the city taxes
    RESUME       = "resume"
    PAUSE        = "pause"
    WHAT_CHANGED = "what_changed"    # 2024 vs 2025 forms
    DASHBOARD    = "dashboard"       # "show me that on the dashboard"
    WHERE_FROM   = "where_from"      # "where did you get that income figure?"
    WHY_DIFFERENT = "why_different"  # "why is that different from the federal return?"
    MISSING      = "missing"         # "what information are we missing?"
    SCANNED      = "scanned"         # "I've finished scanning it" / "read the next supporting document"
    SCAN_FILE    = "scan_file"       # "it's at C:\...\x.pdf"
    NONE_EXISTS  = "none_exists"     # "there isn't one" / "there were no city payments"
    AMOUNT       = "amount"          # "the payment was $400"
    SHOW_FORM    = "show_form"       # "show me the completed city tax form"
    APPROVE      = "approve"
    STATUS       = "status"
    UNKNOWN      = "unknown"


class DeskRequest( BaseModel ):
    model_config = ConfigDict( extra="forbid", frozen=True )

    intent: Intent
    amount: str = ""
    path:   str = ""
    said:   str = ""


_RULES: list[ tuple[ Intent, re.Pattern[ str ]]] = [
    ( Intent.SCAN_FILE,    re.compile( r"(?:[a-z]:\\|/mnt/|/home/|~/)\S+\.(?:pdf|jpe?g|png|tiff?)", re.I )),
    ( Intent.NONE_EXISTS,  re.compile( r"\b(?:there (?:isn'?t|is no|wasn'?t|were no|weren'?t)|no (?:city )?(?:estimated )?payments? (?:were|was) made|"
                                       r"(?:we|they) (?:didn'?t|did not) (?:make|pay) any|doesn'?t exist|none were made)\b", re.I )),
    ( Intent.AMOUNT,       re.compile( r"\b(?:payments?|paid|estimate[sd]?)\b.{0,40}?\$\s?(\d[\d,]*(?:\.\d\d)?)", re.I )),
    ( Intent.APPROVE,      re.compile( r"\b(?:i )?approve (?:it|the (?:draft|return))|\bthe draft is approved\b", re.I )),
    ( Intent.PAUSE,        re.compile( r"\b(?:pause|hold off|stop for now|put it on hold)\b", re.I )),
    ( Intent.RESUME,       re.compile( r"\b(?:resume|pick (?:it )?(?:back )?up|continue) (?:the )?(?:tax|city|prep|work|where)", re.I )),
    ( Intent.SCANNED,      re.compile( r"\b(?:finished|done) scanning\b|\bscanned it\b|\bit'?s (?:in|on) the scanner\b|"
                                       r"\bread the next (?:supporting )?document\b|\bcheck the scan", re.I )),
    ( Intent.SHOW_FORM,    re.compile( r"\bshow me the (?:completed |filled |draft )?(?:city )?(?:tax )?(?:form|return|pdf)\b|"
                                       r"\bopen the (?:draft|form|pdf)\b", re.I )),
    ( Intent.DASHBOARD,    re.compile( r"\b(?:on|to) the dashboard\b|\bshow me (?:that|it|the report)\b", re.I )),
    ( Intent.WHAT_CHANGED, re.compile( r"\bwhat (?:changed|is different|'?s different)\b.{0,40}\b(?:20\d\d|forms?|year)|"
                                       r"\b(?:2024|last year).{0,20}\b(?:2025|this year).{0,20}\bforms?\b", re.I )),
    ( Intent.WHY_DIFFERENT, re.compile( r"\bwhy\b.{0,40}\b(?:different|differ|not the same|lower|higher)\b", re.I )),
    ( Intent.WHERE_FROM,   re.compile( r"\bwhere (?:did|do) (?:you|that|this) (?:get|come)|\bwhat'?s the source\b|\bsource (?:of|for)\b", re.I )),
    ( Intent.MISSING,      re.compile( r"\b(?:what|which) (?:information |documents? |info )?(?:are we |is |are )?(?:still )?"
                                       r"(?:missing|need(?:ed)?|lacking)\b|\bwhat do you (?:still )?need\b", re.I )),
    ( Intent.PREPARE,      re.compile( r"\b(?:prepare|start|do|work on|run)\b.{0,30}\b(?:city tax|grand rapids|city return)", re.I )),
    ( Intent.STATUS,       re.compile( r"\b(?:status|where are (?:we|you)|what are you (?:doing|working on)|how far)\b", re.I )),
]


def read_request( sentence: str ) -> DeskRequest:
    for intent, pattern in _RULES:
        match = pattern.search( sentence )
        if not match:
            continue
        if intent is Intent.AMOUNT:
            return DeskRequest( intent=intent, amount=match.group( 1 ), said=sentence.strip())
        if intent is Intent.SCAN_FILE:
            return DeskRequest( intent=intent, path=match.group( 0 ), said=sentence.strip())
        return DeskRequest( intent=intent, said=sentence.strip())
    return DeskRequest( intent=Intent.UNKNOWN, said=sentence.strip())


def windows_to_wsl( path: str ) -> str:
    """C:\\Users\\x\\a.pdf -> /mnt/c/Users/x/a.pdf; anything else unchanged."""
    if m := re.match( r"^([a-zA-Z]):\\(.*)$", path ):
        return f"/mnt/{ m.group( 1 ).lower()}/" + m.group( 2 ).replace( "\\", "/" )
    return path
