"""KeywordRouter -- deterministic IMessageRouter: who is this for, and what work.

Deterministic on purpose, as the first implementation of the routing step:
a receptionist's model already decided "this goes to the post office"; this
only reads the addressee and the verb out of the user's own words. Replacing
it with a model-backed router is one class implementing IMessageRouter, and
nothing that calls `route()` changes.

Two forms are understood:

* explicit -- `sonnet: build the thing` or `to=opus type=plan | design X`;
* free text -- the first registered agent named (display name or alias),
  ignoring the sender, and a task type inferred from the verbs.
"""
from __future__ import annotations

import os
import re

from ..models import TaskRequest
from ..ports import IAgentRegistry

_EXPLICIT_PREFIX = re.compile( r"^\s*(?P<name>[A-Za-z][\w -]{0,30}?)\s*:\s*(?P<rest>.+)$", re.DOTALL )
_EXPLICIT_FLAGS  = re.compile( r"^\s*(?P<flags>(?:\w+=\S+\s*)+)\|\s*(?P<rest>.+)$", re.DOTALL )

# First match wins, most specific first. Each is (task_type, pattern).
_VERBS: tuple[ tuple[ str, re.Pattern[ str ]], ... ] = (
    ( "status",   re.compile( r"^\s*((what('s| is) the )?(status|progress)\b|(is|has) .* (done|finished)\b|"
                              r"how is .* going|what happened to)", re.I )),
    ( "diagnose", re.compile( r"\b(diagnos\w*|debug\w*|why did .* fail|what went wrong|investigat\w*)\b", re.I )),
    ( "repair",   re.compile( r"\b(repair\w*|fix it|self-repair)\b", re.I )),
    ( "review",   re.compile( r"\b(review\w*|critique|audit)\b", re.I )),
    ( "plan",     re.compile( r"\b(plan\w*|design\w*|architect\w*|propose)\b", re.I )),
    ( "build",    re.compile( r"\b(build\w*|implement\w*|make|create|replicate|write|fix\w*|add|refactor\w*|"
                              r"code|program|port|wire|train|test\w*|deploy\w*)\b", re.I )),
    ( "ask",      re.compile( r"(\?\s*$|^\s*(what|where|why|how|when|who|which|can|could|is|are|does|do)\b)", re.I )),
)


_PATH = re.compile( r"(?<![\w/])(~?/[\w.\-/]+)" )


class KeywordRouter:
    """IMessageRouter over the registry's names and aliases.

    It also reads *where*: an existing directory written in the request, or a
    repository named by one of `repositories`' aliases ("the dashboard",
    "letta-code"), becomes `context.workdir`. With neither, the worker's own
    default applies.
    """

    def __init__( self, registry: IAgentRegistry, default_type: str = "message",
                  repositories: dict[ str, str ] | None = None ) -> None:
        self._registry     = registry
        self._default_type = default_type
        self._repositories = dict( repositories or {})

    def route( self, sender: str, text: str ) -> TaskRequest | None:
        text = text.strip()
        if not text:
            return None
        flags = _EXPLICIT_FLAGS.match( text )
        if flags:
            pairs = dict( p.split( "=", 1 ) for p in flags.group( "flags" ).split())
            name  = self._resolve( pairs.get( "to", "" ))
            if name:
                body = flags.group( "rest" ).strip()
                return self._request( sender, name, body, pairs.get( "type" ) or self.task_type( body ))
        prefix = _EXPLICIT_PREFIX.match( text )
        if prefix:
            name = self._resolve( prefix.group( "name" ))
            if name and name != sender:
                body = prefix.group( "rest" ).strip()
                return self._request( sender, name, body, self.task_type( body ))
        name = self._first_named( sender, text )
        if name is None:
            return None
        return self._request( sender, name, text, self.task_type( text ))

    def task_type( self, text: str ) -> str:
        for task_type, pattern in _VERBS:
            if pattern.search( text ):
                return task_type
        return self._default_type

    # -- internals -----------------------------------------------------------

    def _request( self, sender: str, recipient: str, text: str, task_type: str ) -> TaskRequest:
        workdir = self.workdir( text )
        context = { "workdir": workdir } if workdir else {}
        return TaskRequest( sender=sender, recipient=recipient, task_type=task_type, text=text, context=context )

    def workdir( self, text: str ) -> str | None:
        for found in _PATH.findall( text ):
            path = os.path.expanduser( found.rstrip( ".,;:" ))
            if os.path.isdir( path ):
                return path
        for alias, path in self._repositories.items():
            if re.search( rf"\b{ re.escape( alias ) }\b", text, re.I ):
                return path
        return None

    def _names_for( self, name: str ) -> list[ str ]:
        card = self._registry.card( name )
        if card is None:
            return []
        aliases = [ str( a ) for a in card.settings.get( "aliases", [])]
        return [ name, card.display_name, *aliases ]

    def _resolve( self, spoken: str ) -> str | None:
        wanted = _normal( spoken )
        for name in self._registry.names():
            if wanted in ( _normal( n ) for n in self._names_for( name )):
                return name
        return None

    def _first_named( self, sender: str, text: str ) -> str | None:
        best: tuple[ int, str ] | None = None
        for name in self._registry.names():
            if name == sender:
                continue
            for spoken in self._names_for( name ):
                found = re.search( rf"\b{ re.escape( spoken ) }\b", text, re.I )
                if found and ( best is None or found.start() < best[ 0 ]):
                    best = ( found.start(), name )
        return best[ 1 ] if best else None


def _normal( text: str ) -> str:
    return re.sub( r"^(the|a)\s+", "", text.strip().lower())
