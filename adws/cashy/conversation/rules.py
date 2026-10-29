"""IIntentRule: one reading of a sentence each (Chain of Responsibility).

Order is behaviour: the first rule that recognises a sentence owns it, so
narrower readings come first -- "open the Transfer Activity for the Housing
Gas Bill" is a transaction search before it is a plain "open".
"""
from __future__ import annotations

import re

from browser_workflows.interfaces.templating import IPeriodParser

from ..banks.profiles import COMMON_GOALS
from ..interfaces.banks import IBankDirectory
from ..models import Intent, IntentKind

ORDINALS = { "first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4,
             "fifth": 5, "5th": 5, "sixth": 6, "6th": 6, "seventh": 7, "7th": 7, "eighth": 8, "8th": 8,
             "ninth": 9, "9th": 9, "tenth": 10, "10th": 10, "last": -1 }
_NUMBER = re.compile( rf"\b(?:number|option|no\.?|#)?\s*(\d{{1,2}}|{ '|'.join( ORDINALS ) })\b(?!\s*(?:/|,\s*\d{{4}}|\d))" )
_ACCOUNT = re.compile( r"(?:ending(?: in)?|ends? (?:in|with)|x+|\*+|•|\.\.\.)\s*(\d{4})\b", re.IGNORECASE )


def account_in( text: str ) -> str:
    match = _ACCOUNT.search( text )
    return f"•{ match.group( 1 ) }" if match else ""


def number_in( text: str ) -> int:
    match = _NUMBER.search( text.casefold())
    if not match:
        return 0
    word = match.group( 1 )
    return int( word ) if word.isdigit() else ORDINALS[ word ]


class PhraseRule:
    """A kind, recognised by any of a few anchored patterns."""

    def __init__( self, kind: IntentKind, patterns: list[ str ]) -> None:
        self._kind     = kind
        self._patterns = [ re.compile( p, re.IGNORECASE ) for p in patterns ]

    def match( self, sentence: str ) -> Intent | None:
        if any( p.search( sentence ) for p in self._patterns ):
            return Intent( kind=self._kind, text=sentence )
        return None


class ClickNumberRule:
    """"Click the second matching result", "option 3", "the first one"."""
    _SHAPE = re.compile( r"^(?:(?:click|press|choose|pick|select|open|try)\s+)?(?:on\s+)?(?:the\s+)?"
                         r"(?:number\s+|option\s+|#\s*)?(\w+)(?:\s+(?:matching\s+)?(?:one|result|option|link|button|choice))?$",
                         re.IGNORECASE )

    def match( self, sentence: str ) -> Intent | None:
        found = self._SHAPE.match( sentence.strip())
        if not found:
            return None
        word = found.group( 1 ).casefold()
        if word.isdigit() and 0 < int( word ) <= 99:
            return Intent( kind="click_number", text=sentence, number=int( word ))
        if word in ORDINALS:
            return Intent( kind="click_number", text=sentence, number=ORDINALS[ word ])
        return None


class StatementRule:
    """"Download my September 2025 statement", "get my Sept 2025 Chase statement"."""
    _SHAPE = re.compile( r"\b(?:download|get|fetch|pull|grab|find)\b.*\bstatements?\b|\bstatement\b.*\b(?:for|from)\b"
                         r"|^(?:download|fetch|get) (?:it|that|this|that one) again\b", re.IGNORECASE )

    def __init__( self, banks: IBankDirectory, periods: IPeriodParser ) -> None:
        self._banks   = banks
        self._periods = periods

    def match( self, sentence: str ) -> Intent | None:
        if not self._SHAPE.search( sentence ):
            return None
        bank = self._banks.find( sentence )
        return Intent( kind="get_statement", text=sentence, bank=bank.key if bank else "",
                       period=self._periods.parse( sentence ), account=account_in( sentence ),
                       again=bool( re.search( r"\b(again|another copy|re-?download)\b", sentence, re.IGNORECASE )))


class TransactionRule:
    """"Open the Transfer Activity for the Housing Gas Bill"."""
    _SHAPE = re.compile( r"\b(?:transfer|payment|pay|bill pay)\s+activity\s+(?:for|on|of|about)\s+(?:the\s+|my\s+|our\s+)?(.+?)\.?$",
                         re.IGNORECASE )

    def match( self, sentence: str ) -> Intent | None:
        found = self._SHAPE.search( sentence )
        if not found:
            return None
        return Intent( kind="find_transaction", text=sentence, goal="transfer_activity",
                       target=found.group( 1 ).strip(), account=account_in( sentence ))


class GoToRule:
    """"Open my statements", "take me to transfer activity" -- a goal, by its words."""
    _VERB = re.compile( r"^(?:please\s+)?(?:open|go to|show(?: me)?|take me to|navigate to|find|get me to|bring up)\s+(.+)$",
                        re.IGNORECASE )

    def __init__( self, banks: IBankDirectory ) -> None:
        self._banks = banks

    def match( self, sentence: str ) -> Intent | None:
        found = self._VERB.match( sentence.strip())
        if not found:
            return None
        rest = found.group( 1 ).casefold()
        for goal, vocabulary in COMMON_GOALS.items():
            if goal == "download":
                continue
            if any( re.search( rf"\b{ re.escape( w ) }\b", rest ) for w in vocabulary.words ):
                bank = self._banks.find( sentence )
                return Intent( kind="go_to", text=sentence, goal=goal, bank=bank.key if bank else "",
                               account=account_in( sentence ))
        return None


class OpenBankRule:
    """"Open Chase", "open the Chase login page", "log in to Fifth Third"."""
    _VERB = re.compile( r"\b(?:open|go to|log ?in(?: to)?|sign ?in(?: to)?|start|bring up|pull up)\b", re.IGNORECASE )

    def __init__( self, banks: IBankDirectory ) -> None:
        self._banks = banks

    def match( self, sentence: str ) -> Intent | None:
        bank = self._banks.find( sentence )
        if bank is None or not self._VERB.search( sentence ):
            return None
        return Intent( kind="open_bank", text=sentence, bank=bank.key )


class ClickRule:
    """"Click Statements", "press 'View PDF'"."""
    _SHAPE = re.compile( r"^(?:please\s+)?(?:click|press|tap|select|choose)\s+(?:on\s+)?(?:the\s+)?(.+?)(?:\s+(?:link|button|tab))?\.?$",
                         re.IGNORECASE )

    def match( self, sentence: str ) -> Intent | None:
        found = self._SHAPE.match( sentence.strip())
        if not found:
            return None
        return Intent( kind="click", text=sentence, target=found.group( 1 ).strip( " '\"" ))


class AnswerRule:
    """A bare answer to a question Cashy asked: a month, a bank, an account."""

    def __init__( self, banks: IBankDirectory, periods: IPeriodParser ) -> None:
        self._banks   = banks
        self._periods = periods

    def match( self, sentence: str ) -> Intent | None:
        period  = self._periods.parse( sentence )
        bank    = self._banks.find( sentence )
        account = account_in( sentence )
        if period is None and bank is None and not account:
            return None
        return Intent( kind="answer", text=sentence, period=period, bank=bank.key if bank else "", account=account )


def default_rules( banks: IBankDirectory, periods: IPeriodParser ) -> list:
    return [
        PhraseRule( "confirm", [ r"^(?:yes|yep|yeah|confirm(?:ed)?|go ahead|do it|approved?)\b[.!]?$" ]),
        PhraseRule( "deny", [ r"^(?:no|nope|cancel|don'?t|never mind|nevermind)\b" ]),
        PhraseRule( "stop", [ r"^stop\b", r"^(?:that'?s|that is) (?:enough|it)\b", r"^halt\b" ]),
        PhraseRule( "pause", [ r"^(?:pause|hold on|hang on|wait(?: a (?:sec|second|minute|moment))?)\b" ]),
        PhraseRule( "resume", [ r"^(?:resume|continue|carry on|keep going|pick up where)\b" ]),
        PhraseRule( "try_another", [ r"\btry (?:another|a different|some other) (?:route|way|path|link|option)\b",
                                     r"^try something else\b" ]),
        PhraseRule( "save_adw", [ r"\bsave (?:this|that|the|it)\b.*\b(?:as an? |as )?(?:adw|workflow|playback)\b" ]),
        PhraseRule( "remember", [ r"^remember\b", r"\bremember how (?:we|you) (?:got|did)\b" ]),
        PhraseRule( "go_back", [ r"^(?:go |page )?back\b", r"\bgo back\b" ]),
        PhraseRule( "list_options", [ r"\bwhat (?:options|links|buttons|choices) (?:do you see|are there)\b",
                                      r"\bread (?:me )?(?:the |all )?(?:available )?(?:links|options|buttons|page)\b",
                                      r"\b(?:list|show me) (?:the )?(?:links|options|buttons)\b",
                                      r"\bwhat can (?:i|we|you) click\b" ]),
        PhraseRule( "logged_in", [ r"\b(?:i'?m|i am|we'?re|we are) (?:now )?(?:logged|signed) (?:in|on)\b",
                                   r"^(?:logged|signed) in\b", r"^(?:i'?m|we'?re) in\b", r"^done logging in\b" ]),
        PhraseRule( "status", [ r"^(?:status|where are we|what are you doing|what'?s happening)\b" ]),
        PhraseRule( "help", [ r"^(?:help|what can you do)\b" ]),
        ClickNumberRule(),
        StatementRule( banks, periods ),
        TransactionRule(),
        GoToRule( banks ),
        OpenBankRule( banks ),
        ClickRule(),
        AnswerRule( banks, periods ),
    ]
