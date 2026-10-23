"""receptionist_team.agents -- every agent on the team, defined as a folder.

    agents/
      <agent_name>/
        system_prompt_template/system_prompt.md   # who the agent is
        user_prompt_template/user_prompt.md       # the task / turn template
        model_confiurations/model.json            # model, turns, tools, output
        tools/<tool_name>/tool.json               # declared capabilities (stubs)
        documentation/...                         # generated later (stub)

Toyota herself is one of these folders, not a special case -- her system
prompt is `agents/toyota/system_prompt_template/system_prompt.md` with a
`{{roster}}` placeholder, and `receptionist_team/roles.py` renders it.

This `__init__` is the package's **composition root**: the only module here
allowed to name every concrete class at once. Everything above it talks to
the Protocols in `interfaces.py` and to the SSSF roles re-exported from
`sssf_interfaces`.

    from receptionist_team.agents import build_catalog
    catalog = build_catalog()
    catalog.names()
    catalog.definition( "toyota" ).system_prompt().render({ "roster": ... })
"""
from __future__ import annotations

from pathlib import Path

from sssf_interfaces import ILifecycleLog

from .catalog import DirectoryAgentCatalog, UnknownAgent
from .definitions import FileSystemDefinitionFactory
from .interfaces import IAgentCatalog, IAgentDefinition
from .layout import PART_DIRS, DirectoryAgentLayout
from .lifecycle import SilentLifecycleLog, StdoutLifecycleLog
from .models import DocumentationIndex, ModelConfiguration, ToolDeclaration
from .role_adapter import AgentRoleFactory, IncompleteAgentDefinition

AGENTS_DIR = Path( __file__ ).resolve().parent


def build_catalog(
    root: Path | None = None,
    logger: ILifecycleLog | None = None,
) -> IAgentCatalog:
    """Wire the concrete loaders into a catalog. The one spot that may."""
    reporting = logger if logger is not None else SilentLifecycleLog()
    layout = DirectoryAgentLayout()
    return DirectoryAgentCatalog(
        root=root if root is not None else AGENTS_DIR,
        layout=layout,
        factory=FileSystemDefinitionFactory( layout, reporting ),
        logger=reporting,
    )


# Module-level default catalog, built lazily so importing this package does
# not touch the filesystem. roles.py and extra_roles.py both read the same
# one, which is what makes a folder the single definition of an agent.
_default: IAgentCatalog | None = None


def catalog() -> IAgentCatalog:
    """The shared catalog over this directory."""
    global _default
    if _default is None:
        _default = build_catalog()
    return _default


__all__ = [
    "AGENTS_DIR",
    "PART_DIRS",
    "AgentRoleFactory",
    "DirectoryAgentCatalog",
    "DirectoryAgentLayout",
    "DocumentationIndex",
    "FileSystemDefinitionFactory",
    "IAgentCatalog",
    "IAgentDefinition",
    "IncompleteAgentDefinition",
    "ModelConfiguration",
    "SilentLifecycleLog",
    "StdoutLifecycleLog",
    "ToolDeclaration",
    "UnknownAgent",
    "build_catalog",
    "catalog",
]
