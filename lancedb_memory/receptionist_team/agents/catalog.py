"""DirectoryAgentCatalog -- discovery.

Single responsibility: decide which subdirectories of `agents/` are agents,
and hand each one to a factory. It does not know what a definition is made of
(definitions.py) nor where the parts live (layout.py).

A directory counts as an agent when it holds at least one of the layout's
part directories. That rule is what keeps `__pycache__`, this package's own
`.py` files, and a stray notes folder out of the roster without a hardcoded
ignore list.
"""
from __future__ import annotations

from pathlib import Path

from sssf_interfaces import ILifecycleLog, LifecycleLogged

from .interfaces import IAgentDefinition, IAgentLayout
from .layout import PART_DIRS


class UnknownAgent( KeyError ):
    """Asked for an agent that is not on disk."""


class DirectoryAgentCatalog( LifecycleLogged ):
    """IAgentCatalog over one directory of agent folders."""

    def __init__(
        self,
        root: Path,
        layout: IAgentLayout,
        factory,                      # IAgentDefinitionFactory: .build( name, root )
        logger: ILifecycleLog,
    ) -> None:
        LifecycleLogged.__init__( self, logger )
        self._root = root
        self._layout = layout
        self._factory = factory
        self._cache: dict[ str, IAgentDefinition ] = {}

    @property
    def root( self ) -> Path:
        return self._root

    def names( self ) -> tuple[ str, ... ]:
        if not self._root.is_dir():
            return ()
        return tuple(
            child.name
            for child in sorted( self._root.iterdir())
            if self._is_agent_dir( child )
        )

    def has( self, name: str ) -> bool:
        return name in self.names()

    def definition( self, name: str ) -> IAgentDefinition:
        if name not in self._cache:
            if not self.has( name ):
                raise UnknownAgent(
                    f"No agent folder named { name!r } under { self._root }. "
                    f"Known: { ', '.join( self.names()) or '(none)' }"
                )
            self._cache[ name ] = self._factory.build( name, self._root / name )
        return self._cache[ name ]

    def _is_agent_dir( self, path: Path ) -> bool:
        if not path.is_dir() or path.name.startswith( ( ".", "__" )):
            return False
        return any( ( path / part ).is_dir() for part in PART_DIRS )
