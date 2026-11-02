"""IChangeTracker over git plumbing, without touching the user's index or files.

A snapshot is a git tree id written from a *temporary* index (GIT_INDEX_FILE),
so `git add -A` there stages nothing in the workspace's real index, and no
checkout, reset, stash or clean ever runs. The one thing it does leave behind
is git objects in `.git/objects` (loose blobs and trees), which `git gc` prunes.

Files git ignores are not part of a snapshot, so a change to one is not reported.
Hooks, fsmonitor, external diff drivers and textconv are all switched off: the
workspace's own configuration decides nothing here.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from ..models import ChangedPath, ChangeSet

SAFE = [ "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "core.quotePath=false" ]
STATUS = { "A": "added", "M": "modified", "D": "deleted", "R": "renamed", "C": "copied", "T": "type_changed" }


class GitChangeTracker:
    def __init__( self, git: str = "git", timeout_seconds: float = 60.0 ) -> None:
        self._git, self._timeout = git, timeout_seconds

    def snapshot( self, workdir: Path ) -> str:
        with tempfile.TemporaryDirectory( prefix="bridge_index_" ) as tmp:
            env = { **os.environ, "GIT_INDEX_FILE": str( Path( tmp ) / "index" )}
            if self._run( workdir, [ "rev-parse", "--verify", "-q", "HEAD^{tree}" ], check=False ).returncode == 0:
                self._run( workdir, [ "read-tree", "HEAD" ], env=env )
            self._run( workdir, [ "add", "-A", "--", "." ], env=env )
            return self._run( workdir, [ "write-tree" ], env=env ).stdout.decode().strip()

    def changes( self, workdir: Path, baseline: str ) -> ChangeSet:
        now   = self.snapshot( workdir )
        names = self._run( workdir, [ "diff-tree", "-r", "-M", "--name-status", "-z", baseline, now ]).stdout
        diff  = self._run( workdir, [ "diff-tree", "-r", "-M", "-p", "--no-ext-diff", "--no-textconv", "--no-color",
                                      baseline, now ]).stdout.decode( "utf-8", "replace" )
        return ChangeSet( paths=_parse( names ), diff=diff )

    def _run( self, workdir: Path, args: list[ str ], env: dict[ str, str ] | None = None,
              check: bool = True ) -> subprocess.CompletedProcess:
        done = subprocess.run([ self._git, *SAFE, "-C", str( workdir ), *args ], capture_output=True,
                              env=env, timeout=self._timeout )
        if check and done.returncode != 0:
            raise RuntimeError( f"git { args[ 0 ]} failed: { done.stderr.decode( 'utf-8', 'replace' ).strip()[ :200 ]}" )
        return done


def _parse( raw: bytes ) -> list[ ChangedPath ]:
    """`--name-status -z`: STATUS\\0path\\0, or Rnnn\\0old\\0new\\0 for renames and copies."""
    parts = raw.decode( "utf-8", "surrogateescape" ).split( "\0" )
    out, i = [], 0
    while i < len( parts ) and parts[ i ]:
        code = parts[ i ][ 0 ]
        if code in "RC":
            out.append( ChangedPath( path=parts[ i + 2 ], status=STATUS[ code ], old_path=parts[ i + 1 ]))
            i += 3
        else:
            out.append( ChangedPath( path=parts[ i + 1 ], status=STATUS.get( code, "modified" )))
            i += 2
    return out
