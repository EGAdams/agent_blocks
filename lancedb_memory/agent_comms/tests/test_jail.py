"""BwrapJail and WorkspacePrivatePaths.

Offline tests build a small git workspace and check what would be hidden and
the order of the bubblewrap arguments. The last test runs real bubblewrap
when it is installed, with canaries: it is the check that matters.

    cd lancedb_memory
    .venv/bin/python -m agent_comms.tests.test_jail
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from agent_comms.executors.jail import BwrapJail, WorkspacePrivatePaths
from agent_comms.models import JailUnavailable, Masks

BWRAP = shutil.which( "bwrap" )


def workspace( base: Path ) -> Path:
    root = base / "ws"
    for relative, text in {
        ".gitignore":                    "data/\n*.log\nmixed/cache/\n__pycache__/\n",
        "src/app.py":                    "print( 'hello' )\n",
        "src/__pycache__/app.pyc":       "",
        "src/run.log":                   "private log\n",
        "data/table/part1.lance":        "rows\n",
        "mixed/kept.py":                 "x = 1\n",
        "mixed/cache/a/b.bin":           "blob\n",
        "adws/adw_data/recording.json":  "clicks\n",
        "adws/tool.py":                  "y = 2\n",
        "conf/.env":                     "GROQ_API_KEY=not-a-real-key\n",
        "conf/worker_token":             "not-a-real-token\n",
        "conf/settings.toml":            "ok = true\n",
        "store/books.sqlite":            "rows\n",
    }.items():
        path = root / relative
        path.parent.mkdir( parents=True, exist_ok=True )
        path.write_text( text )
    subprocess.run([ "git", "init", "-q", str( root )], check=True )
    subprocess.run([ "git", "-C", str( root ), "add", "src/app.py", "mixed/kept.py", ".gitignore", "adws/tool.py" ], check=True )
    return root


def relative( masks: Masks, root: Path ) -> tuple[ set[ str ], set[ str ]]:
    return ({ str( p.relative_to( root )) for p in masks.dirs }, { str( p.relative_to( root )) for p in masks.files })


def test_private_paths_are_few_and_cover_the_data() -> None:
    root  = workspace( Path( tempfile.mkdtemp( prefix="jail_test_" )))
    dirs, files = relative( WorkspacePrivatePaths().masks( root ), root )
    assert "data" in dirs                       # widened from data/table/part1.lance to one mount
    assert "mixed/cache" in dirs                # stops below mixed/, which still holds kept.py
    assert "adws/adw_data" in dirs              # always private, though git does not ignore it
    assert { "src/run.log", "conf/.env", "conf/worker_token", "store/books.sqlite" } <= files
    assert not any( "__pycache__" in d for d in dirs ) and "src" not in dirs and "mixed" not in dirs
    assert "conf/settings.toml" not in files and "src/app.py" not in files


def test_not_a_git_repo_still_hides_secret_names() -> None:
    root = Path( tempfile.mkdtemp( prefix="jail_test_" ))
    ( root / ".env" ).write_text( "K=v\n" )
    ( root / "a.py" ).write_text( "" )
    dirs, files = relative( WorkspacePrivatePaths().masks( root ), root )
    assert files == { ".env" } and dirs == set()


def test_token_and_auth_backups_are_hidden_but_token_code_is_not() -> None:
    root = Path( tempfile.mkdtemp( prefix="jail_test_" ))
    for name in ( "provider_token_backup_1.json", "codex_auth_backup_local_2.json", "auth.json",
                  "claude_sdk_token_rate.py", "oauth.json", "tokenizer.json" ):
        ( root / name ).write_text( "{}" )
    _, files = relative( WorkspacePrivatePaths().masks( root ), root )
    assert files == { "provider_token_backup_1.json", "codex_auth_backup_local_2.json", "auth.json" }, files


def test_git_failure_means_no_jail_not_a_thinner_one() -> None:
    root = workspace( Path( tempfile.mkdtemp( prefix="jail_test_" )))
    try:
        WorkspacePrivatePaths( git="/nonexistent/git" ).masks( root )
    except JailUnavailable:
        return
    raise AssertionError( "a workspace git cannot read must refuse, not hide less" )


class FixedPaths:
    def __init__( self, masks: Masks ) -> None:
        self.value = masks

    def masks( self, root: Path ) -> Masks:
        return self.value


def test_argument_order_lets_later_mounts_win() -> None:
    fake_bwrap = Path( tempfile.mkdtemp()) / "bwrap"
    fake_bwrap.write_text( "#!/bin/sh\n" )
    fake_bwrap.chmod( 0o755 )
    root, home = Path( "/home/eg/ws" ), Path( "/home/eg" )
    masks = Masks( dirs=[ root / "data" ], files=[ root / ".env" ])
    argv  = BwrapJail( FixedPaths( masks ), state_dirs=[], bwrap=str( fake_bwrap ), home=home ).wrap(
        [ "/usr/bin/codex", "exec", "-" ], root, root / "src", [ Path( "/tmp/out" )])
    at = lambda *pair: next( i for i in range( len( argv )) if argv[ i:i + len( pair )] == list( pair ))
    assert at( "--tmpfs", "/home" ) < at( "--ro-bind", str( root ), str( root )) < at( "--tmpfs", str( root / "data" ))
    assert at( "--tmpfs", "/tmp" ) < at( "--bind", "/tmp/out", "/tmp/out" )
    assert at( "--ro-bind", "/dev/null", str( root / ".env" )) > at( "--ro-bind", str( root ), str( root ))
    assert "--clearenv" in argv and argv[ argv.index( "--chdir" ) + 1 ] == str( root / "src" )
    assert argv[ -3: ] == [ "/usr/bin/codex", "exec", "-" ]


def test_env_is_set_and_a_missing_login_refuses() -> None:
    fake_bwrap = Path( tempfile.mkdtemp()) / "bwrap"
    fake_bwrap.write_text( "#!/bin/sh\n" )
    fake_bwrap.chmod( 0o755 )
    home = Path( tempfile.mkdtemp( prefix="codex_home_" ))
    jail = BwrapJail( FixedPaths( Masks()), state_dirs=[ home ], bwrap=str( fake_bwrap ),
                      env={ "CODEX_HOME": str( home )}, required=[ home / "auth.json" ])
    try:
        jail.wrap([ "/usr/bin/true" ], Path( "/" ), Path( "/" ), [])
        raise AssertionError( "no login file must mean no run" )
    except JailUnavailable:
        pass
    ( home / "auth.json" ).write_text( "{}" )
    argv = jail.wrap([ "/usr/bin/true" ], Path( "/" ), Path( "/" ), [])
    i = argv.index( "CODEX_HOME" )
    assert argv[ i - 1 ] == "--setenv" and argv[ i + 1 ] == str( home ) and i > argv.index( "--clearenv" )


def test_a_file_bind_is_read_only_and_lands_after_its_state_dir() -> None:
    fake_bwrap = Path( tempfile.mkdtemp()) / "bwrap"
    fake_bwrap.write_text( "#!/bin/sh\n" )
    fake_bwrap.chmod( 0o755 )
    state = Path( tempfile.mkdtemp( prefix="claude_bridge_" ))
    login = Path( "/home/eg/.claude/.credentials.json" )
    argv  = BwrapJail( FixedPaths( Masks()), state_dirs=[ state ], bwrap=str( fake_bwrap ),
                       file_binds=[( login, state / ".credentials.json" )]).wrap([ "/usr/bin/true" ], Path( "/" ), Path( "/" ), [])
    at = lambda *pair: next( i for i in range( len( argv )) if argv[ i:i + len( pair )] == list( pair ))
    assert at( "--bind", str( state ), str( state )) < at( "--ro-bind", str( login ), str( state / ".credentials.json" ))
    assert [ "--bind", str( login ), str( state / ".credentials.json" )] not in [ argv[ i:i + 3 ] for i in range( len( argv ))]


def test_writable_root_keeps_masks_and_git_on_top() -> None:
    fake_bwrap = Path( tempfile.mkdtemp()) / "bwrap"
    fake_bwrap.write_text( "#!/bin/sh\n" )
    fake_bwrap.chmod( 0o755 )
    root  = Path( tempfile.mkdtemp( prefix="ws_" ))
    ( root / ".git" ).mkdir()
    masks = Masks( dirs=[ root / "data" ], files=[ root / ".env" ])
    argv  = BwrapJail( FixedPaths( masks ), state_dirs=[], bwrap=str( fake_bwrap ), root_writable=True ).wrap(
        [ "/usr/bin/true" ], root, root, [])
    at = lambda *pair: next( i for i in range( len( argv )) if argv[ i:i + len( pair )] == list( pair ))
    assert at( "--bind", str( root ), str( root )) < at( "--tmpfs", str( root / "data" ))
    assert at( "--ro-bind", "/dev/null", str( root / ".env" )) < at( "--ro-bind", str( root / ".git" ), str( root / ".git" ))
    assert [ "--ro-bind", str( root ), str( root )] not in [ argv[ i:i + 3 ] for i in range( len( argv ))]


def test_missing_bubblewrap_refuses() -> None:
    jail = BwrapJail( FixedPaths( Masks( dirs=[], files=[])), state_dirs=[], bwrap="/nonexistent/bwrap" )
    try:
        jail.wrap([ "/usr/bin/true" ], Path( "/" ), Path( "/" ), [])
    except JailUnavailable:
        return
    raise AssertionError( "no bubblewrap must mean no run" )


def test_real_bubblewrap_hides_what_it_should() -> None:
    if BWRAP is None:
        print( "  (skipped: bubblewrap not installed)" )
        return
    base    = Path( tempfile.mkdtemp( prefix=".jail_test_", dir=Path.home()))
    try:
        root    = workspace( base )
        outside = base / "outside_canary.txt"
        outside.write_text( "OUTSIDE-CANARY\n" )
        out     = Path( tempfile.mkdtemp( prefix="jail_out_" ))
        script  = ( f"cat { outside } 2>/dev/null || echo OUTSIDE-HIDDEN; "
                    f"cat { root }/data/table/part1.lance 2>/dev/null || echo DATA-HIDDEN; "
                    f"test -s { root }/conf/.env && echo ENV-LEAKED || echo ENV-HIDDEN; "
                    f"cat { root }/src/app.py; "
                    f"touch { root }/src/new.py 2>/dev/null && echo WROTE || echo NOWRITE; "
                    f"echo answer > { out }/answer.txt; "
                    f"env | grep -c SECRET_FOR_TEST || true" )
        os.environ[ "SECRET_FOR_TEST" ] = "x"
        argv = BwrapJail( WorkspacePrivatePaths(), state_dirs=[], bwrap=BWRAP ).wrap(
            [ "/bin/sh", "-c", script ], root, root, [ out ])
        done = subprocess.run( argv, capture_output=True, text=True, timeout=30 )
        seen = done.stdout.split()
        assert "OUTSIDE-HIDDEN" in seen and "OUTSIDE-CANARY" not in seen, done
        assert "DATA-HIDDEN" in seen and "ENV-HIDDEN" in seen, done
        assert "print(" in done.stdout and "NOWRITE" in seen, done
        assert seen[ -1 ] == "0", done
        assert ( out / "answer.txt" ).read_text() == "answer\n"
    finally:
        os.environ.pop( "SECRET_FOR_TEST", None )
        shutil.rmtree( base )


def test_real_bubblewrap_writable_root() -> None:
    if BWRAP is None:
        print( "skip: bubblewrap not installed" )
        return
    base = Path( tempfile.mkdtemp( prefix="jail_rw_" ))
    try:
        root = workspace( base )
        script = ( "echo new > made.md && echo WROTE; "
                   "( echo x > .git/HOOKED ) 2>/dev/null && echo GIT-WRITABLE || echo GIT-READONLY; "
                   "cat conf/.env 2>/dev/null | grep -q GROQ && echo ENV-SEEN || echo ENV-HIDDEN" )
        argv = BwrapJail( WorkspacePrivatePaths(), state_dirs=[], bwrap=BWRAP, root_writable=True ).wrap(
            [ "/bin/sh", "-c", script ], root, root, [])
        done = subprocess.run( argv, capture_output=True, text=True, timeout=30 )
        seen = done.stdout.split()
        assert "WROTE" in seen and ( root / "made.md" ).read_text() == "new\n", done
        assert "GIT-READONLY" in seen and not ( root / ".git" / "HOOKED" ).exists(), done
        assert "ENV-HIDDEN" in seen, done
        assert ( root / "conf" / ".env" ).read_text().startswith( "GROQ" ), "the masked file itself is untouched"
    finally:
        shutil.rmtree( base )



def test_network_off_adds_unshare_net() -> None:
    base = Path( tempfile.mkdtemp( prefix="jail_net_" ))
    try:
        root = workspace( base )
        fake = base / "bwrap"
        fake.write_text( "#!/bin/sh\n" )
        fake.chmod( 0o755 )
        on  = BwrapJail( WorkspacePrivatePaths(), state_dirs=[], bwrap=str( fake )).wrap([ "true" ], root, root, [])
        off = BwrapJail( WorkspacePrivatePaths(), state_dirs=[], bwrap=str( fake ), network=False ).wrap(
            [ "true" ], root, root, [])
        assert "--unshare-net" not in on and "--unshare-net" in off
    finally:
        shutil.rmtree( base )


def test_linked_dns_settings_are_bound_only_with_network() -> None:
    base = Path( tempfile.mkdtemp())
    try:
        root, fake = base / "ws", base / "bwrap"
        root.mkdir()
        fake.write_text( "#!/bin/sh\n" )
        fake.chmod( 0o755 )
        real = base / "wsl" / "resolv.conf"
        real.parent.mkdir()
        real.write_text( "nameserver 10.0.0.1\n" )
        link = base / "resolv.conf"
        link.symlink_to( real )
        make = lambda net: BwrapJail( WorkspacePrivatePaths(), state_dirs=[], bwrap=str( fake ), network=net,
                                      dns_files=( str( link ), "/no/such/resolv.conf" )).wrap([ "true" ], root, root, [])
        on, off = make( True ), make( False )
        at = on.index( str( real ))
        assert on[ at - 1 : at + 2 ] == [ "--ro-bind", str( real ), str( real )]
        assert str( real ) not in off
        assert "/no/such/resolv.conf" not in on
    finally:
        shutil.rmtree( base )


def test_real_bubblewrap_can_resolve_names() -> None:
    """The jail must resolve names the way the host does, or codex cannot reach OpenAI."""
    if not shutil.which( "bwrap" ) or not shutil.which( "getent" ):
        return
    host = subprocess.run([ "getent", "hosts", "chatgpt.com" ], capture_output=True, timeout=30 )
    if host.returncode != 0:
        return                                          # no DNS on the host either: nothing to compare
    root = Path( tempfile.mkdtemp())
    try:
        argv = BwrapJail( WorkspacePrivatePaths(), state_dirs=[], home=root ).wrap(
            [ "getent", "hosts", "chatgpt.com" ], root, root, [])
        assert subprocess.run( argv, capture_output=True, timeout=30 ).returncode == 0
    finally:
        shutil.rmtree( root )


TESTS = [ v for k, v in dict( globals()).items() if k.startswith( "test_" )]

if __name__ == "__main__":
    for test in TESTS:
        test()
        print( "ok", test.__name__ )
    print( f"{ len( TESTS ) } passed" )
