"""The SSSF interface vocabulary, importable from an ADW in this directory.

Single responsibility: *locate* the factory's `adw_interface_prompt.py` and
re-export the roles it defines, so an interface-style ADW here can say

    from sssf_roles import IApplication, IPhase, IPhaseExecutor, LifecycleLogged

instead of restating the interfaces at the top of every file. Nothing here
invents an interface of its own: if a role changes shape it changes in the
template and every ADW follows automatically. That is the whole point of the
indirection, and it is the same job `lancedb_memory/sssf_interfaces/` does for
the receptionist packages -- this is the copy that sits where `uv run`
adws/adw_*.py can reach it.

The template is a `uv run` script with a PEP 723 header, not a package, so a
plain `import` cannot find it; it is loaded by file location. It does
`from adw_modules import agents, session, utils` internally, and because THIS
directory's `adw_modules` is already imported by the time an ADW asks for a
role, the template binds to this workspace's copy rather than the factory's.
That is deliberate: two parallel `adw_modules` trees exist, and an ADW must
run against the one next to it.

Override the search with SSSF_ADWS_DIR when the factory lives elsewhere.
Failure is loud and names the fix rather than degrading into a local copy of
the interfaces: a silent fallback is exactly how the two definitions would
drift, which is what this module exists to prevent.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

_MODULE_FILENAME = "adw_interface_prompt.py"

# Searched in order; the first directory that actually holds the template wins.
_CANDIDATE_DIRS = (
    "/home/adamsl/super-simple-software-factory/.claude/skills/sssf/templates/adws",
    "/home/adamsl/agent_blocks/.claude/skills/sssf/templates/adws",
)

# This directory, so our own adw_modules is the one the template binds to.
_HERE = Path( __file__ ).resolve().parent


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

    # Ours first: the template's own `from adw_modules import ...` must resolve
    # to this workspace's adw_modules, not the factory's parallel copy.
    if str( _HERE ) not in sys.path:
        sys.path.insert( 0, str( _HERE ))

    spec = importlib.util.spec_from_file_location(
        "adw_interface_prompt", adws_dir() / _MODULE_FILENAME
    )
    if spec is None or spec.loader is None:
        raise ImportError( f"Could not build an import spec for { _MODULE_FILENAME }" )
    module = importlib.util.module_from_spec( spec )
    sys.modules[ "adw_interface_prompt" ] = module
    try:
        spec.loader.exec_module( module )
    except BaseException:
        del sys.modules[ "adw_interface_prompt" ]
        raise
    return module


_template = _load()

# Roles (interfaces) -- the vocabulary an ADW programs against.
IApplication      = _template.IApplication
IArgumentParser   = _template.IArgumentParser
IConfigLoader     = _template.IConfigLoader
ILifecycleLog     = _template.ILifecycleLog
IPhase            = _template.IPhase
IPhaseExecutor    = _template.IPhaseExecutor
IPhaseHandle      = _template.IPhaseHandle
IPromptResolver   = _template.IPromptResolver
ISessionFactory   = _template.ISessionFactory

# Shipped implementations worth reusing as-is rather than rewriting. The
# executor is the one that matters: it drives run.phase()'s context-manager
# protocol by hand, so an interface-style ADW needs no `with` block.
LifecycleLogged             = _template.LifecycleLogged
PrintLifecycleLog           = _template.PrintLifecycleLog
ConsoleLifecycleLog         = _template.ConsoleLifecycleLog
ManualContextPhaseExecutor  = _template.ManualContextPhaseExecutor
SssfConfigLoader            = _template.SssfConfigLoader
SessionRunFactory           = _template.SessionRunFactory
FileOrInlinePromptResolver  = _template.FileOrInlinePromptResolver

# Values (not behavior).
PromptRequest = _template.PromptRequest

__all__ = [
    "ConsoleLifecycleLog",
    "FileOrInlinePromptResolver",
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
    "ManualContextPhaseExecutor",
    "PrintLifecycleLog",
    "PromptRequest",
    "SessionRunFactory",
    "SssfConfigLoader",
    "adws_dir",
]
