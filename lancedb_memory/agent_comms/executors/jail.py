"""BwrapJail -- what a CLI agent may see of this machine while it works.

Codex's own `--sandbox read-only` stops writes, not reads. Left alone it can
read anything EG's account can -- ~/liz_city_tax and ~/probate_case among
them -- and whatever it reads travels to OpenAI with the prompt. BwrapJail
runs it under bubblewrap in a filesystem built from four things only:

  * /usr and /etc, read-only: the programs and their configuration;
  * the workspace, read-only, minus its private data (WorkspacePrivatePaths);
  * the agent's own state directory, read-write, because codex keeps its
    ChatGPT login there and rotates the refresh token in place. For the
    bridge that is a dedicated CODEX_HOME (~/.codex-bridge) with its own
    login and nothing else -- never EG's ~/.codex, which also holds his past
    Codex conversations, history and memories;
  * the scratch directory the answer is written to.

The rest of /home is an empty tmpfs, /tmp is fresh, Windows' /mnt/c is not
there at all, and the environment is cleared so the API keys in EG's shell
never reach the agent. The network is left alone: codex has to reach OpenAI.
A jail built with `network=False` (the bridge's post-write checks) gets none.
With the network on, the DNS settings come too: under WSL /etc/resolv.conf
is a link to /mnt/wsl/resolv.conf, which binding /etc alone leaves dangling,
so that one file is bound at its real path (and nothing else under /mnt).

A write-enabled card builds its jail with `root_writable=True`: the
workspace is then bound read-write, but the private-path masks still sit on
top of it and its `.git` stays read-only, so a task can change the files it
was given and nothing that decides what runs next (hooks, git config).

Nothing here is optional at run time. If bubblewrap is missing, a required
file (the bridge's own login) is absent, or the private paths cannot be
worked out, `wrap` raises JailUnavailable and the
task fails; it never falls back to running the agent unjailed.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from ..models import JailUnavailable, Masks
from ..ports import IPrivatePaths

# Hidden whatever git thinks of them: conversation memory, agent mailboxes,
# ADW run data (browser recordings, sssf.db, session logs), browser page
# snapshots, and a copied ~/.claude. Some of these are tracked, by accident.
ALWAYS_PRIVATE = ( ".claude", "mailbox", ".playwright-mcp", "adws/adw_data", "adws/adws/adw_data",
                   "lancedb_memory/lance_data", "lancedb_memory/nexus" )

# Ignored because they are rebuilt, not because they are private. Masking
# every __pycache__ would cost a mount each and protect nothing.
REBUILDABLE = frozenset({ "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache", ".mypy_cache", ".git" })

# Names that hold secrets or private records wherever they turn up, tracked or not.
PRIVATE_NAME = re.compile( r"""(?ix)
      ^\.env(\..+)?$  |  \.env$                     # dotenv files
    | credentials     |  secret                     # .credentials.json, client_secret.json
    | (^|[._-])tokens?$  |  \.token$                # worker_token, client_token
    | (^|[._-])(tokens?|auth)([._-].*)?\.json$      # provider_token_backup_1.json, codex_auth_backup.json
    | ^id_(rsa|dsa|ecdsa|ed25519)                   # ssh keys
    | \.(pem|key|p12|pfx|kdbx)$                     # keys and key stores
    | \.(db|sqlite3?)(-wal|-shm|-journal)?$         # databases: somebody's records
""" )


class WorkspacePrivatePaths:
    """IPrivatePaths for a git workspace: what to hide, as few mounts as possible.

    Three sources, merged: ALWAYS_PRIVATE; everything git ignores (minus
    REBUILDABLE), each entry widened to the largest directory holding nothing
    git still sees, so a table's thousand data files cost one mount, not a
    thousand; and every file or directory named like a secret (PRIVATE_NAME).
    """

    def __init__( self, git: str = "git", timeout_seconds: float = 30.0 ) -> None:
        self._git     = git
        self._timeout = timeout_seconds

    def masks( self, root: Path ) -> Masks:
        root    = root.resolve()
        visible = self._git_lines( root, "ls-files", "-z", "--cached", "--others", "--exclude-standard" )
        ignored = self._git_lines( root, "ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--directory" )
        keep    = { str( parent ) for line in visible for parent in Path( line ).parents }
        found   = [ root / relative for relative in ALWAYS_PRIVATE ]
        found  += [ root / _widen( Path( line.rstrip( "/" )), keep ) for line in ignored if not _rebuildable( line )]
        found  += _named_private( root )
        return _merge( found )

    def _git_lines( self, root: Path, *args: str ) -> list[ str ]:
        if not ( root / ".git" ).exists():
            return []
        try:
            done = subprocess.run([ self._git, "-C", str( root ), *args ], capture_output=True, timeout=self._timeout )
        except ( OSError, subprocess.TimeoutExpired ) as error:
            raise JailUnavailable( f"could not ask git what to hide: { error }" ) from error
        if done.returncode != 0:
            raise JailUnavailable( f"git { args[ 0 ]} failed in { root }: { done.stderr.decode( errors='replace' ).strip()[ :200 ]}" )
        return [ line for line in done.stdout.decode( "utf-8", errors="surrogateescape" ).split( "\0" ) if line ]


def _rebuildable( line: str ) -> bool:
    path = Path( line.rstrip( "/" ))
    return path.suffix == ".pyc" or any( part in REBUILDABLE for part in path.parts )


def _widen( path: Path, keep: set[ str ]) -> Path:
    """Climb while the parent directory holds nothing git still shows."""
    while path.parent != Path( "." ) and str( path.parent ) not in keep:
        path = path.parent
    return path


def _named_private( root: Path ) -> list[ Path ]:
    found: list[ Path ] = []
    for here, dirs, files in os.walk( root ):
        for name in list( dirs ):
            if name in REBUILDABLE:
                dirs.remove( name )
            elif PRIVATE_NAME.search( name ):
                found.append( Path( here ) / name )
                dirs.remove( name )
        found += [ Path( here ) / name for name in files if PRIVATE_NAME.search( name )]
    return found


def _merge( found: list[ Path ]) -> Masks:
    """Drop what is missing, symlinked or already inside a hidden directory."""
    dirs: list[ Path ] = []
    files: list[ Path ] = []
    for path in sorted( set( found ), key=lambda p: len( p.parts )):
        if path.is_symlink() or not path.exists():
            continue                # a link's target is hidden where it really lives
        if any( hidden in path.parents for hidden in dirs ):
            continue
        ( dirs if path.is_dir() else files ).append( path )
    return Masks( dirs=sorted( dirs ), files=sorted( files ))


class BwrapJail:
    """ICommandJail over bubblewrap. See the module docstring for what is inside."""

    SYSTEM = ( "/usr", "/etc" )
    LINKS  = (( "usr/bin", "/bin" ), ( "usr/sbin", "/sbin" ), ( "usr/lib", "/lib" ), ( "usr/lib64", "/lib64" ))
    PATH   = "/usr/local/bin:/usr/bin:/bin"
    DNS    = ( "/etc/resolv.conf", )

    def __init__( self, private: IPrivatePaths, state_dirs: list[ Path ], bwrap: str = "/usr/bin/bwrap",
                  home: Path | None = None, env: dict[ str, str ] | None = None,
                  required: list[ Path ] | None = None, root_writable: bool = False,
                  network: bool = True, dns_files: tuple[ str, ... ] | None = None ) -> None:
        self._private  = private
        self._dns      = tuple( self.DNS if dns_files is None else dns_files )
        self._network  = network
        self._root_rw  = root_writable
        self._state    = [ Path( d ) for d in state_dirs ]
        self._bwrap    = bwrap
        self._home     = Path( home or Path.home())
        self._env      = dict( env or {})
        self._required = [ Path( r ) for r in required or []]

    def _dns_targets( self ) -> list[ str ]:
        """Real files behind the DNS settings that the /usr and /etc binds do not already carry."""
        out = []
        for name in self._dns:
            real   = os.path.realpath( name )
            inside = any( real == top or real.startswith( top + "/" ) for top in self.SYSTEM )
            if not inside and os.path.isfile( real ) and real not in out:
                out.append( real )
        return out

    def wrap( self, argv: list[ str ], root: Path, cwd: Path, writable: list[ Path ]) -> list[ str ]:
        if not os.access( self._bwrap, os.X_OK ):
            raise JailUnavailable( f"bubblewrap is not installed at { self._bwrap }" )
        for path in self._required:
            if not path.is_file():
                raise JailUnavailable( f"{ path } is missing (not logged in?)" )
        masks = self._private.masks( root )
        jail  = [ self._bwrap, "--die-with-parent", "--new-session",
                  "--unshare-pid", "--unshare-ipc", "--unshare-uts" ]
        if not self._network:
            jail.append( "--unshare-net" )
        for path in self.SYSTEM:
            jail += [ "--ro-bind", path, path ]
        if self._network:
            for path in self._dns_targets():
                jail += [ "--ro-bind", path, path ]
        for target, link in self.LINKS:
            jail += [ "--symlink", target, link ]
        jail += [ "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--tmpfs", str( self._home.parent )]
        for path in self._state:
            if path.is_dir():
                jail += [ "--bind", str( path ), str( path )]
        jail += [ "--bind" if self._root_rw else "--ro-bind", str( root ), str( root )]
        for path in masks.dirs:
            jail += [ "--tmpfs", str( path ), "--remount-ro", str( path )]
        for path in masks.files:
            jail += [ "--ro-bind", "/dev/null", str( path )]
        if self._root_rw and ( root / ".git" ).exists():
            jail += [ "--ro-bind", str( root / ".git" ), str( root / ".git" )]
        for path in writable:
            jail += [ "--bind", str( path ), str( path )]
        jail += [ "--clearenv", "--setenv", "HOME", str( self._home ), "--setenv", "USER", self._home.name,
                  "--setenv", "PATH", self.PATH, "--setenv", "LANG", "C.UTF-8" ]
        for name, value in sorted( self._env.items()):
            jail += [ "--setenv", name, value ]
        return jail + [ "--chdir", str( cwd )] + argv
