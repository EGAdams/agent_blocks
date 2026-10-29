"""Reading the page and choosing on it. The page is untrusted input: its
words are matched, never obeyed."""
from __future__ import annotations

from typing import Protocol

from ..models import BankProfile, GoalVocabulary, PageOption, PageSnapshot, RankedOption, Risk
from .browser import ICashyBrowser


class IDomReader( Protocol ):
    def snapshot( self, browser: ICashyBrowser, bank: BankProfile | None ) -> PageSnapshot: ...


class IOptionRanker( Protocol ):
    """Best match first for the words a person (or a goal) used."""

    def rank( self, options: list[ PageOption ], words: list[ str ]) -> list[ RankedOption ]: ...


class IRiskPolicy( Protocol ):
    """Would pressing this do something that cannot be taken back?"""

    def assess( self, option: PageOption ) -> Risk: ...


class IArrivalCheck( Protocol ):
    """Is the page now the destination a goal names?"""

    def arrived( self, browser: ICashyBrowser, goal: GoalVocabulary ) -> bool: ...
