"""Sentences into intents (Chain of Responsibility)."""
from __future__ import annotations

from typing import Protocol

from ..models import Intent


class IIntentRule( Protocol ):
    """One way of reading a sentence; None hands it to the next rule."""

    def match( self, sentence: str ) -> Intent | None: ...


class IIntentInterpreter( Protocol ):
    """A whole message -> one intent per sentence that asked for something."""

    def interpret( self, text: str ) -> list[ Intent ]: ...
