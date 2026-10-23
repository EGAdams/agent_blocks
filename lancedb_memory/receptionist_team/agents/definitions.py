"""FileSystemAgentDefinition -- one agent folder, assembled.

Single responsibility: composition. It holds the five collaborators an agent
definition is made of and hands each question to the one that owns it; it
reads no file and parses no syntax itself. Every collaborator arrives through
its interface, so a definition backed by a database or a zip file is a
constructor change, not an edit here.

Lifecycle reporting comes from the SSSF template's own `LifecycleLogged`
mixin (see sssf_interfaces) rather than a second local one.
"""
from __future__ import annotations

from pathlib import Path

from sssf_interfaces import ILifecycleLog, LifecycleLogged

from .interfaces import (
    IAgentLayout,
    IDocumentationSet,
    IModelConfigurationSource,
    IPromptTemplate,
    IToolCatalog,
)
from .models import DocumentationIndex, ModelConfiguration, ToolDeclaration


class FileSystemAgentDefinition( LifecycleLogged ):
    """IAgentDefinition assembled from one directory's worth of collaborators."""

    def __init__(
        self,
        name: str,
        root: Path,
        system_prompt: IPromptTemplate,
        user_prompt: IPromptTemplate,
        model_configuration: IModelConfigurationSource,
        tools: IToolCatalog,
        documentation: IDocumentationSet,
        logger: ILifecycleLog,
    ) -> None:
        LifecycleLogged.__init__( self, logger )
        self._name = name
        self._root = root
        self._system_prompt = system_prompt
        self._user_prompt = user_prompt
        self._model_configuration = model_configuration
        self._tools = tools
        self._documentation = documentation

    @property
    def name( self ) -> str:
        return self._name

    @property
    def root( self ) -> Path:
        return self._root

    def system_prompt( self ) -> IPromptTemplate:
        return self._system_prompt

    def user_prompt( self ) -> IPromptTemplate:
        return self._user_prompt

    def model_configuration( self ) -> ModelConfiguration:
        return self._model_configuration.configuration()

    def tools( self ) -> tuple[ ToolDeclaration, ... ]:
        return self._tools.declarations()

    def documentation( self ) -> DocumentationIndex:
        return self._documentation.index()


class FileSystemDefinitionFactory( LifecycleLogged ):
    """Builds a FileSystemAgentDefinition for an agent root.

    This is the one place that names the concrete loaders, which keeps
    `catalog.py` about *discovery* and this about *assembly* -- the two
    reasons a definition-loading change arrives.
    """

    def __init__( self, layout: IAgentLayout, logger: ILifecycleLog ) -> None:
        LifecycleLogged.__init__( self, logger )
        self._layout = layout
        self._logger = logger

    def build( self, name: str, root: Path ) -> FileSystemAgentDefinition:
        # Imported here, not at module scope, so the Protocol-only half of
        # this package stays importable without the concrete half.
        from .documentation import DirectoryDocumentationSet
        from .model_configurations import JsonModelConfigurationSource
        from .prompt_templates import FilePromptTemplate
        from .tool_catalog import DirectoryToolCatalog

        return FileSystemAgentDefinition(
            name=name,
            root=root,
            system_prompt=FilePromptTemplate( self._layout.system_prompt_file( root )),
            user_prompt=FilePromptTemplate( self._layout.user_prompt_file( root )),
            model_configuration=JsonModelConfigurationSource(
                self._layout.model_configuration_file( root )
            ),
            tools=DirectoryToolCatalog( self._layout.tools_dir( root )),
            documentation=DirectoryDocumentationSet( self._layout.documentation_dir( root )),
            logger=self._logger,
        )
