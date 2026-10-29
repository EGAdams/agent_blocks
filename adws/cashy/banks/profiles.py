"""Which banks Cashy knows, as data. A new bank is one BankProfile here.

`login_url` must be the bank's own, typed from its official site: Cashy
opens only these, never a link a search or a page handed it, so a
look-alike domain cannot be reached by asking for "the Chase login".
`hosts` are the domains a tab must be on for Cashy to treat it as the bank.
"""
from __future__ import annotations

from ..models import BankProfile, GoalVocabulary

#: Destinations every bank shares; a profile's own `goals` override these.
COMMON_GOALS: dict[ str, GoalVocabulary ] = {
    "statements": GoalVocabulary(
        words=[ "statements & documents", "statements and documents", "statements", "e-statements",
                "estatements", "documents", "account documents", "view statements" ],
        arrived=[ "statements" ]),
    "transfer_activity": GoalVocabulary(
        words=[ "transfer activity", "transfer history", "payment activity", "activity", "transfers",
                "pay & transfer", "pay and transfer", "bill pay activity" ],
        arrived=[ "activity", "history" ]),
    "accounts": GoalVocabulary(
        words=[ "accounts", "account summary", "my accounts", "overview" ], arrived=[ "accounts", "summary" ]),
    "download": GoalVocabulary(
        words=[ "download", "save as pdf", "pdf", "save", "view pdf" ], arrived=[]),
}

CHASE = BankProfile(
    key="chase", title="Chase", names=[ "chase", "jpmorgan", "jp morgan" ],
    login_url="https://secure.chase.com/web/auth/dashboard",
    hosts=[ "chase.com" ],
    goals={ "statements": GoalVocabulary( words=[ "statements & documents", "statements", "documents" ],
                                          arrived=[ "statements" ])},
)

FIFTH_THIRD = BankProfile(
    key="fifth_third", title="Fifth Third", names=[ "fifth third", "5/3", "fifth 3rd", "5th 3rd" ],
    login_url="https://www.53.com/content/fifth-third/en/login.html",
    hosts=[ "53.com" ],
)

BANK_OF_AMERICA = BankProfile(
    key="bank_of_america", title="Bank of America", names=[ "bank of america", "bofa", "boa", "b of a" ],
    login_url="https://secure.bankofamerica.com/login/sign-in/signOnV2Screen.go",
    hosts=[ "bankofamerica.com" ],
)

ALL_BANKS: list[ BankProfile ] = [ CHASE, FIFTH_THIRD, BANK_OF_AMERICA ]
