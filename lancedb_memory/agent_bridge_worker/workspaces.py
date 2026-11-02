"""ConfiguredWorkspaces: IWorkspaceResolver over `workspaces.json`, with write scope.

A workspace is a *name* from the task; the directory comes only from this
machine's config. A write-enabled workspace must be a git repository directly
under the config's `write_root` (or named exactly in `extra_write_paths`, the
one way to approve a project that lives elsewhere), reached without a symlink anywhere on the way,
and holding none of the files that decide what the bridge may do (this
package, the post office's cards, the gateway, the tokens). An entry that
fails those checks is not write-enabled -- it is reported, and read-only work
keeps running. Every check is repeated on every `resolve()`, because a
directory can change between the worker starting and a task arriving.
"""
from __future__ import annotations

import json
from pathlib import Path

from .models import WorkspaceConfig, WorkspaceEntry


def _no_symlinks( path: Path ) -> bool:
    """True when `path` is absolute and no component of it is a symlink (so resolving changes nothing)."""
    return path.is_absolute() and Path( path ).resolve() == Path( path ) and not path.is_symlink()


def _overlaps( a: Path, b: Path ) -> bool:
    a, b = a.resolve(), b.resolve()
    return a == b or a in b.parents or b in a.parents


class ConfiguredWorkspaces:
    def __init__( self, config: WorkspaceConfig, protected: list[ Path ]) -> None:
        self._config    = config
        self._root      = Path( config.write_root )
        self._extra     = frozenset( Path( p ) for p in config.extra_write_paths )
        self._protected = [ Path( p ) for p in protected ]

    @classmethod
    def load( cls, path: Path, protected: list[ Path ]) -> "ConfiguredWorkspaces":
        return cls( WorkspaceConfig.model_validate( json.loads( Path( path ).read_text( encoding="utf-8" ))), protected )

    # -- IWorkspaceResolver -----------------------------------------------------

    def resolve( self, workspace: str ) -> Path:
        entry = self._config.workspaces.get( workspace )
        if entry is None:
            raise LookupError( f"no workspace called { workspace } on this machine" )
        path = Path( entry.path )
        if not path.is_absolute():
            raise LookupError( f"workspace { workspace } is not configured with an absolute path" )
        if not _no_symlinks( path ):
            raise LookupError( f"workspace { workspace } is reached through a symlink; refused" )
        if not path.is_dir():
            raise LookupError( f"workspace { workspace } is not a directory here" )
        return path

    # -- write scope --------------------------------------------------------------

    def write_problem( self, workspace: str ) -> str | None:
        """Why `workspace` may not be written to, or None when it may."""
        entry = self._config.workspaces.get( workspace )
        if entry is None:
            return f"no workspace called { workspace } on this machine"
        if not entry.writes:
            return f"workspace { workspace } is read-only on this machine"
        try:
            path = self.resolve( workspace )
        except LookupError as exc:
            return str( exc )
        if not _no_symlinks( self._root ) or not self._root.is_dir():
            return f"the write root { self._root } is missing or reached through a symlink"
        if path.parent != self._root and path not in self._extra:
            return f"workspace { workspace } is not directly under the write root { self._root }"
        if not ( path / ".git" ).is_dir() or ( path / ".git" ).is_symlink():
            return f"workspace { workspace } is not a git repository, so its changes could not be shown"
        for guarded in self._protected:
            if guarded.exists() and _overlaps( path, guarded ):
                return f"workspace { workspace } overlaps { guarded }, which controls the bridge"
        return None

    def write_names( self ) -> frozenset[ str ]:
        return frozenset( name for name in self._config.workspaces if self.write_problem( name ) is None )

    def problems( self ) -> dict[ str, str ]:
        """Write-enabled entries that are not usable, and why -- for the worker's startup line."""
        out = {}
        for name, entry in self._config.workspaces.items():
            if entry.writes:
                problem = self.write_problem( name )
                if problem:
                    out[ name ] = problem
        return out

    def checks( self, workspace: str ) -> list[ list[ str ]]:
        entry: WorkspaceEntry | None = self._config.workspaces.get( workspace )
        return [ list( c ) for c in entry.checks ] if entry else []
