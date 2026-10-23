"""Roles (interfaces) for the on-disk agent definitions.

`typing.Protocol`, not ABC, matching agent_memory/ports.py and the house DIP
rules -- the SSSF template's own roles are ABCs and are imported as such from
`sssf_interfaces`; these are ours and follow the local convention. Names are
`ISomething`, never `*Port`.

Nothing in this module imports pathlib for behavior, reads a file, or names a
concrete class. Each Protocol below is one seam:

    IAgentLayout            where the parts of a definition live on disk
    IPromptTemplate         text with {{placeholders}} and how to fill them
    IModelConfigurationSource  how a ModelConfiguration is obtained
    IToolCatalog            what tools an agent declares
    IDocumentationSet       what documentation an agent carries
    IAgentDefinition        one agent, assembled from the five above
    IAgentCatalog           the set of agents on disk
    IRoleFactory            definition -> an executable finance_team AgentRole
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from .models import DocumentationIndex, ModelConfiguration, ToolDeclaration


class IAgentLayout( Protocol ):
    """The only knowledge of folder *names* in this package.

    Every method takes the agent's own root directory, so the layout never
    needs to know where the catalog lives.
    """

    def system_prompt_dir( self, root: Path ) -> Path: ...
    def user_prompt_dir( self, root: Path ) -> Path: ...
    def model_configuration_dir( self, root: Path ) -> Path: ...
    def tools_dir( self, root: Path ) -> Path: ...
    def documentation_dir( self, root: Path ) -> Path: ...


class IPromptTemplate( Protocol ):
    """Prompt text plus its placeholder contract."""

    def raw( self ) -> str: ...
    def placeholders( self ) -> tuple[ str, ... ]: ...
    def render( self, values: Mapping[ str, str ] ) -> str: ...


class IModelConfigurationSource( Protocol ):
    def configuration( self ) -> ModelConfiguration: ...


class IToolCatalog( Protocol ):
    def declarations( self ) -> tuple[ ToolDeclaration, ... ]: ...


class IDocumentationSet( Protocol ):
    def index( self ) -> DocumentationIndex: ...


class IAgentDefinition( Protocol ):
    """One agent folder, as the rest of the system sees it."""

    @property
    def name( self ) -> str: ...

    @property
    def root( self ) -> Path:
        """The agent's own directory. In the Protocol because `cli.py validate`
        reports which part directories are missing, which it cannot do from the
        parts alone."""

    def system_prompt( self ) -> IPromptTemplate: ...
    def user_prompt( self ) -> IPromptTemplate: ...
    def model_configuration( self ) -> ModelConfiguration: ...
    def tools( self ) -> tuple[ ToolDeclaration, ... ]: ...
    def documentation( self ) -> DocumentationIndex: ...


class IAgentCatalog( Protocol ):
    def names( self ) -> tuple[ str, ... ]: ...
    def has( self, name: str ) -> bool: ...
    def definition( self, name: str ) -> IAgentDefinition: ...


class IRoleFactory( Protocol ):
    """Turns a definition into something `finance_team`'s runner can execute.

    The return type is deliberately not abstracted: `AgentRole` is a frozen
    data value, and the house rule says don't put an interface in front of
    one of those.
    """

    def role( self, definition: IAgentDefinition, values: Mapping[ str, str ] | None = None ): ...
