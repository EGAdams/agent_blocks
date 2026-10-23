"""The SSSF interface vocabulary, as an importable module.

Single responsibility: *locate* `adws/adw_interface_prompt.py` and re-export
the roles it defines. Nothing here invents an interface of its own -- if a
name below needs to change shape, it changes in the SSSF template and every
consumer in this workspace follows automatically. That is the whole point of
the indirection: `receptionist_team/agents/` programs against
`ILifecycleLog`, `IApplication`, `IPhase` and friends without knowing they
live inside a skill template directory rather than a published package.

The template is a `uv run` script with a PEP 723 header, not a package, so it
cannot be reached with a plain `import`. It is loaded by file location, with
its own directory put on `sys.path` first because it does
`from adw_modules import ...` internally.

Override the search with SSSF_ADWS_DIR when the factory lives elsewhere:

    SSSF_ADWS_DIR=/path/to/adws .venv/bin/python -m receptionist_team.agents.cli list

Requires `python-dotenv`, `pyyaml` and `rich` in the venv -- `adw_modules`
pulls them in. Failure is loud and names the fix rather than degrading into a
local copy of the interfaces: a silent fallback would let the two definitions
drift, which is exactly what this module exists to prevent.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

_MODULE_FILENAME = "adw_interface_prompt.py"

# Searched in order. The first directory that actually holds the interface
# template wins; agent_blocks' own copy of the skill is an older template set
# that predates the file, hence the search rather than a single constant.
_CANDIDATE_DIRS = (
    "/home/adamsl/super-simple-software-factory/.claude/skills/sssf/templates/adws",
    "/home/adamsl/agent_blocks/.claude/skills/sssf/templates/adws",
)


def adws_dir() -> Path:
    """The directory holding adw_interface_prompt.py, or raise saying why not."""
    override = os.getenv( "SSSF_ADWS_DIR" )
    candidates = ( override, ) if override else _CANDIDATE_DIRS
    for candidate in candidates:
        path = Path( candidate )
        if ( path / _MODULE_FILENAME ).is_file():
            return path
    raise ImportError(
        f"Could not find { _MODULE_FILENAME } in any of: "
        f"{ ', '.join( str( c ) for c in candidates )}. "
        "Set SSSF_ADWS_DIR to the super-simple-software-factory adws directory."
    )


def _load():
    """Import the template once and cache it under its own module name."""
    cached = sys.modules.get( "adw_interface_prompt" )
    if cached is not None:
        return cached

    directory = adws_dir()
    # adw_interface_prompt.py imports `adw_modules.*` as a top-level package,
    # so its own directory has to be importable before it is executed.
    if str( directory ) not in sys.path:
        sys.path.insert( 0, str( directory ))

    spec = importlib.util.spec_from_file_location(
        "adw_interface_prompt", directory / _MODULE_FILENAME
    )
    if spec is None or spec.loader is None:
        raise ImportError( f"Could not build an import spec for { directory / _MODULE_FILENAME }" )
    module = importlib.util.module_from_spec( spec )
    sys.modules[ "adw_interface_prompt" ] = module
    try:
        spec.loader.exec_module( module )
    except BaseException:
        del sys.modules[ "adw_interface_prompt" ]
        raise
    return module


_template = _load()

# Roles (interfaces) -- the vocabulary everything else programs against.
IApplication      = _template.IApplication
IArgumentParser   = _template.IArgumentParser
IConfigLoader     = _template.IConfigLoader
ILifecycleLog     = _template.ILifecycleLog
IPhase            = _template.IPhase
IPhaseExecutor    = _template.IPhaseExecutor
IPhaseHandle      = _template.IPhaseHandle
IPromptResolver   = _template.IPromptResolver
ISessionFactory   = _template.ISessionFactory

# Shipped implementations worth reusing as-is rather than rewriting.
LifecycleLogged      = _template.LifecycleLogged
PrintLifecycleLog    = _template.PrintLifecycleLog
ConsoleLifecycleLog  = _template.ConsoleLifecycleLog

# Values (not behavior) -- carried through for completeness.
PromptRequest = _template.PromptRequest

__all__ = [
    "IApplication",
    "IArgumentParser",
    "IConfigLoader",
    "ILifecycleLog",
    "IPhase",
    "IPhaseExecutor",
    "IPhaseHandle",
    "IPromptResolver",
    "ISessionFactory",
    "LifecycleLogged",
    "PrintLifecycleLog",
    "ConsoleLifecycleLog",
    "PromptRequest",
    "adws_dir",
]
