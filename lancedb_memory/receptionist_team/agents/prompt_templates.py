"""Prompt templates: text on disk plus a `{{placeholder}}` contract.

Single responsibility: placeholder syntax. Nothing else in this package knows
that `{{` means anything.

`{{name}}` is used rather than `<<NAME>>` because `<<CONVERSATION>>` and
`<<MESSAGE>>` already belong to agent_memory's TemplatePromptComposer, and a
user prompt template is handed to that composer *unrendered*. Two delimiters,
two owners, no collision.

Rendering fails loud on a placeholder nobody supplied a value for -- the same
posture as receptionist_team/roles.py raising at import on an unregistered
role. A prompt silently shipped with "{{roster}}" still in it is worse than a
crash, because the agent would run with a visibly broken system prompt.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

_PLACEHOLDER_RE = re.compile( r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}" )


class MissingPlaceholderValue( RuntimeError ):
    """A template placeholder had no value supplied."""


class TextPromptTemplate:
    """IPromptTemplate over an in-memory string. The behavior lives here."""

    def __init__( self, text: str, origin: str = "<memory>" ) -> None:
        self._text = text
        self._origin = origin

    def raw( self ) -> str:
        return self._text

    def origin( self ) -> str:
        """Where this text came from, for error messages. Not part of IPromptTemplate."""
        return self._origin

    def placeholders( self ) -> tuple[ str, ... ]:
        seen: list[ str ] = []
        for match in _PLACEHOLDER_RE.finditer( self._text ):
            name = match.group( 1 )
            if name not in seen:
                seen.append( name )
        return tuple( seen )

    def render( self, values: Mapping[ str, str ] ) -> str:
        missing = [ name for name in self.placeholders() if name not in values ]
        if missing:
            raise MissingPlaceholderValue(
                f"{ self._origin } needs { sorted( missing )}; "
                f"got { sorted( values )}"
            )
        return _PLACEHOLDER_RE.sub( lambda m: values[ m.group( 1 )], self._text )


class FilePromptTemplate:
    """IPromptTemplate over one file, read lazily and cached.

    Lazy because building a catalog of twenty agents should not read forty
    prompt files; cached because a definition is asked for its prompt once per
    turn in the worst case.
    """

    def __init__( self, path: Path ) -> None:
        self._path = path
        self._delegate: TextPromptTemplate | None = None

    @property
    def path( self ) -> Path:
        return self._path

    def exists( self ) -> bool:
        return self._path.is_file()

    def _loaded( self ) -> TextPromptTemplate:
        if self._delegate is None:
            if not self._path.is_file():
                raise FileNotFoundError( f"No prompt template at { self._path }" )
            self._delegate = TextPromptTemplate(
                self._path.read_text( encoding="utf-8" ), origin=str( self._path )
            )
        return self._delegate

    def raw( self ) -> str:
        return self._loaded().raw()

    def placeholders( self ) -> tuple[ str, ... ]:
        return self._loaded().placeholders()

    def render( self, values: Mapping[ str, str ] ) -> str:
        return self._loaded().render( values )
