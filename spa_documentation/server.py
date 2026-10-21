#!/usr/bin/env python3
"""Local dev server for spa_documentation.

Same static-file behavior as `python3 -m http.server`, plus local API routes:

    GET /api/git-status?item=<docs-item-path>
        -> { "item": ..., "exists": bool, "dirty": bool, "docs_missing": bool }

    POST /api/run-update  {"item": <docs-item-path>}
        -> { "ok": bool, "error"?: str }

    GET /api/run-update-status?item=<docs-item-path>
        -> { "running": bool, "exit_code": int|null, "log_tail": str }

    POST /api/run-<lesson>-tests
        -> { "ok": bool, "terminal": "Windows Terminal", "profile": "Ubuntu-26.04" }

    POST /api/open-<lesson>-project-terminal
        -> { "ok": bool, "terminal": "Windows Terminal", "profile": "Ubuntu-26.04" }

/api/git-status runs `git status --porcelain` on that item's real source file
(see doc_source_map.py for the item-path -> source-file mapping) and also
checks whether the item's generated docs (Source / Class Diagram / Sequence
Diagram) are missing or still empty skeletons. "dirty" is true if either is
the case -- a clean git diff alone doesn't mean the docs were ever generated,
so a never-touched source file with never-generated docs must still count as
needing an update. This is what drives the "Update Documentation" tab's
button (enabled + red when dirty, greyed out when clean) -- app.js calls this
once each time a detail tab page loads, not on a timer.

/api/run-update is what the button's click handler actually calls: it starts
update_documentation_agent.py (in .venv, the real Claude Agent SDK call) for
that item as a background subprocess and returns immediately. app.js then
polls /api/run-update-status until the process exits, showing the tail of its
output, then refreshes /api/git-status and reloads the report. Only one run
per item at a time -- a second click while one's in flight is rejected, not
queued or restarted.

<lesson> is a slug from LESSON_PROJECTS below -- one entry per Construction
Status lesson page that carries an "Open Terminal in Project" button and a
"how to check this yourself" button. Both routes are argument-only: the slug
selects a fixed directory and a fixed script, and nothing from the request
body reaches a shell.

Needs .venv (claude_agent_sdk, pygments) for the subprocess it launches, but
this file itself is stdlib only.
"""
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from doc_source_map import DOCS_ROOT, docs_missing, resolve_source_path

VENV_PYTHON = DOCS_ROOT / ".venv" / "bin" / "python"
UPDATE_SCRIPT = DOCS_ROOT / "update_documentation_agent.py"
RUNS_DIR = DOCS_ROOT / ".update_runs"
AGENT_BLOCKS_ROOT = DOCS_ROOT.parent
DECLARE_PLUG_IN_POINT_DIR = (
    DOCS_ROOT
    / "voice_communication"
    / "conversation_agent"
    / "basic_agent_construction_status"
    / "declare_the_plug_in_point"
)
# The Browser Tests plan documents this repo's own browser_tests/ suite, so
# its lesson pages live under spa_browser_tests/ -- browser_tests/ is already
# the source directory.
BROWSER_TESTS_STATUS_DIR = (
    DOCS_ROOT
    / "spa_browser_tests"
    / "characterization_suite"
    / "basic_agent_construction_status"
)
DECLARE_TEST_SEAMS_DIR = BROWSER_TESTS_STATUS_DIR / "declare_the_test_seams"
CHARACTERIZE_LEGACY_SPA_DIR = BROWSER_TESTS_STATUS_DIR / "characterize_the_legacy_spa"

# One entry per lesson page with terminal buttons. Adding a lesson is a row
# here plus its check script -- not another pair of hand-written routes.
LESSON_PROJECTS = {
    "interface-file": {
        "title": "Interface File",
        "directory": DECLARE_PLUG_IN_POINT_DIR / "interface_file",
        "test_script": DOCS_ROOT / "scripts" / "run_interface_file_tests.sh",
    },
    "event-contracts": {
        "title": "Event Contracts",
        "directory": DECLARE_PLUG_IN_POINT_DIR / "event_contracts",
        "test_script": DOCS_ROOT / "scripts" / "run_event_contracts_tests.sh",
    },
    "characterization-suite": {
        "title": "Characterization Suite",
        "directory": CHARACTERIZE_LEGACY_SPA_DIR / "characterization_suite",
        "test_script": DOCS_ROOT / "scripts" / "run_browser_tests_checks.sh",
    },
    "platform-profile": {
        "title": "Platform Profile",
        "directory": DECLARE_TEST_SEAMS_DIR / "platform_profile",
        "test_script": DOCS_ROOT / "scripts" / "run_browser_tests_checks.sh",
    },
}
WINDOWS_TERMINAL_PROFILE = "Ubuntu-26.04"
WSL_DISTRO = "Ubuntu-26.04"
WINDOWS_MOUNT_ROOT = Path( "/mnt" )
WINDOWS_SYSTEM32 = WINDOWS_MOUNT_ROOT / "c" / "Windows" / "System32"
WINDOWS_SESSION_ENV_KEYS = (
    "DISPLAY",
    "PULSE_SERVER",
    "WAYLAND_DISPLAY",
    "WSL2_GUI_APPS_ENABLED",
    "WSLENV",
    "WSL_DISTRO_NAME",
    "WSL_INTEROP",
    "WT_PROFILE_ID",
    "WT_SESSION",
    "XDG_RUNTIME_DIR",
)

# item -> {"proc": Popen, "log_path": Path, "log_file": file, "started": float}.
# One in-flight run per item; guarded by _runs_lock since ThreadingHTTPServer
# handles requests concurrently.
_runs: dict = {}
_runs_lock = threading.Lock()


def _start_update_run( item: str ) -> dict:
    RUNS_DIR.mkdir( exist_ok=True )
    log_path = RUNS_DIR / ( item.replace( "/", "__" ) + ".log" )
    log_file = open( log_path, "wb" )

    # Local Claude Code subscription auth, not direct-API billing -- same
    # requirement as update_documentation_agent.py's own header comment.
    env = dict( os.environ )
    env.pop( "ANTHROPIC_API_KEY", None )
    env.pop( "ANTHROPIC_BASE_URL", None )

    proc = subprocess.Popen(
        [ str( VENV_PYTHON ), str( UPDATE_SCRIPT ), item ],
        cwd=str( DOCS_ROOT ),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=env,
    )
    return { "proc": proc, "log_path": log_path, "log_file": log_file, "started": time.time() }


def _as_wsl_path( windows_path: str ) -> str | None:
    if len( windows_path ) < 3 or windows_path[ 1:3 ] != ":\\":
        return None
    drive = windows_path[ 0 ].lower()
    relative_path = windows_path[ 3: ].replace( "\\", "/" )
    return str( WINDOWS_MOUNT_ROOT / drive / relative_path )


def _find_windows_executable( name: str ) -> str | None:
    executable = shutil.which( name )
    if executable is not None:
        return executable

    system_executable = WINDOWS_SYSTEM32 / name
    if system_executable.is_file():
        return str( system_executable )

    # The systemd user service intentionally has a Linux-only PATH. Ask
    # Windows to resolve per-user app execution aliases such as wt.exe rather
    # than hard-coding the Windows account name into this server.
    cmd = WINDOWS_SYSTEM32 / "cmd.exe"
    if not cmd.is_file():
        return None
    result = subprocess.run(
        [ str( cmd ), "/d", "/s", "/c", "where", name ],
        cwd=str( WINDOWS_SYSTEM32 ),
        capture_output=True,
        text=True,
        check=False,
    )
    for candidate in result.stdout.splitlines():
        wsl_path = _as_wsl_path( candidate.strip() )
        if wsl_path is not None and os.path.isfile( wsl_path ):
            return wsl_path
    return None


def _windows_terminal_environment() -> dict:
    env = dict( os.environ )
    session_candidates: list[ tuple[ int, dict[ str, str ] ] ] = []

    # A systemd user service starts before Windows Terminal and therefore does
    # not inherit that terminal's WSL interop socket. At click time, borrow only
    # the desktop/interop variables from an active interactive shell. Do not
    # copy its full environment; it can contain credentials unrelated to this
    # fixed launcher.
    for environ_path in Path( "/proc" ).glob( "[0-9]*/environ" ):
        try:
            process_name = ( environ_path.parent / "comm" ).read_text().strip()
            if process_name not in { "bash", "fish", "zsh" }:
                continue
            entries = environ_path.read_bytes().split( b"\0" )
        except ( OSError, UnicodeError ):
            continue

        process_env: dict[ str, str ] = {}
        for entry in entries:
            if b"=" not in entry:
                continue
            key, value = entry.split( b"=", 1 )
            process_env[ key.decode( "utf-8", errors="replace" ) ] = value.decode(
                "utf-8",
                errors="replace",
            )

        interop = process_env.get( "WSL_INTEROP", "" )
        if not process_env.get( "WT_SESSION" ) or not os.path.exists( interop ):
            continue
        session_candidates.append(( int( environ_path.parent.name ), process_env ))

    if session_candidates:
        _, session_env = max( session_candidates, key=lambda candidate: candidate[ 0 ] )
        for key in WINDOWS_SESSION_ENV_KEYS:
            value = session_env.get( key )
            if value is not None:
                env[ key ] = value

    return env


def _open_windows_terminal(
    working_directory: Path,
    title: str,
    command: tuple[ str, ... ] = (),
) -> None:
    windows_terminal = _find_windows_executable( "wt.exe" )
    wsl = _find_windows_executable( "wsl.exe" )
    if windows_terminal is None or wsl is None:
        raise RuntimeError( "Windows Terminal or WSL is not available from this environment" )
    launch_env = _windows_terminal_environment()
    if not launch_env.get( "WSL_INTEROP" ) or not launch_env.get( "WT_SESSION" ):
        raise RuntimeError(
            "No active Windows Terminal-backed WSL session is available; open Ubuntu-26.04 once and retry",
        )

    # The browser cannot start a desktop application itself. This fixed,
    # argument-only launch is deliberately server-side: no command or path is
    # accepted from the request.
    terminal_arguments = [
        windows_terminal,
        "-w",
        "new",
        "new-tab",
        "--profile",
        WINDOWS_TERMINAL_PROFILE,
        "--title",
        title,
        # This is a command for Windows Terminal to resolve on the Windows
        # side. Passing the mounted Linux path (/mnt/c/.../wsl.exe) makes
        # the terminal tab exit before the WSL command ever starts.
        "wsl.exe",
        "-d",
        WSL_DISTRO,
        "--cd",
        str( working_directory ),
    ]
    if command:
        terminal_arguments.extend([ "--", *command ])

    terminal_process = subprocess.Popen(
        terminal_arguments,
        cwd=str( DOCS_ROOT ),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=launch_env,
    )
    threading.Thread( target=terminal_process.wait, daemon=True ).start()


def _open_lesson_test_terminal( lesson: dict ) -> None:
    script = lesson[ "test_script" ]
    if not script.is_file():
        raise RuntimeError( f"{lesson[ 'title' ]} test script is missing" )
    # The script ends with `exec bash`, leaving the Ubuntu tab open and usable
    # after every check finishes.
    _open_windows_terminal(
        AGENT_BLOCKS_ROOT,
        f"{lesson[ 'title' ]} Tests",
        ( "bash", str( script ) ),
    )


def _open_lesson_project_terminal( lesson: dict ) -> None:
    directory = lesson[ "directory" ]
    if not directory.is_dir():
        raise RuntimeError( f"{lesson[ 'title' ]} documentation directory is missing" )
    _open_windows_terminal( directory, f"{lesson[ 'title' ]} Project" )


class DocsRequestHandler( SimpleHTTPRequestHandler ):
    # SimpleHTTPRequestHandler sends no Cache-Control header, so browsers
    # fall back to heuristic caching off Last-Modified and can silently
    # serve a stale copy of a page that was just edited. These docs are
    # edited constantly, so force revalidation on every request instead.
    def end_headers( self ) -> None:
        self.send_header( "Cache-Control", "no-cache" )
        super().end_headers()

    def do_GET( self ) -> None:
        parsed = urllib.parse.urlparse( self.path )
        if parsed.path == "/api/git-status":
            self._handle_git_status( parsed )
            return
        if parsed.path == "/api/run-update-status":
            self._handle_run_update_status( parsed )
            return
        super().do_GET()

    def do_POST( self ) -> None:
        parsed = urllib.parse.urlparse( self.path )
        if parsed.path == "/api/run-update":
            self._handle_run_update()
            return
        if parsed.path.startswith( "/api/run-" ) and parsed.path.endswith( "-tests" ):
            self._handle_lesson_terminal(
                parsed.path[ len( "/api/run-" ): -len( "-tests" ) ],
                _open_lesson_test_terminal,
            )
            return
        if (
            parsed.path.startswith( "/api/open-" )
            and parsed.path.endswith( "-project-terminal" )
        ):
            self._handle_lesson_terminal(
                parsed.path[ len( "/api/open-" ): -len( "-project-terminal" ) ],
                _open_lesson_project_terminal,
            )
            return
        self.send_error( 404 )

    def _read_json_body( self ) -> dict:
        length = int( self.headers.get( "Content-Length", "0" ) )
        raw = self.rfile.read( length ) if length else b"{}"
        return json.loads( raw or b"{}" )

    def _send_json( self, payload: dict, status: int = 200 ) -> None:
        body = json.dumps( payload ).encode( "utf-8" )
        self.send_response( status )
        self.send_header( "Content-Type", "application/json" )
        self.send_header( "Content-Length", str( len( body ) ) )
        self.end_headers()
        self.wfile.write( body )

    def _handle_run_update( self ) -> None:
        try:
            data = self._read_json_body()
        except json.JSONDecodeError:
            self._send_json( { "ok": False, "error": "invalid JSON body" }, 400 )
            return

        item = data.get( "item", "" )
        doc_dir = DOCS_ROOT / item if item else None
        source = resolve_source_path( item ) if item else None

        if not item or source is None or not doc_dir.is_dir():
            self._send_json( { "ok": False, "error": f"unknown item '{item}'" }, 400 )
            return

        with _runs_lock:
            existing = _runs.get( item )
            if existing and existing[ "proc" ].poll() is None:
                self._send_json( { "ok": False, "error": "already running" }, 409 )
                return
            _runs[ item ] = _start_update_run( item )

        self._send_json( { "ok": True } )

    def _handle_lesson_terminal( self, slug: str, open_terminal ) -> None:
        lesson = LESSON_PROJECTS.get( slug )
        if lesson is None:
            self._send_json( { "ok": False, "error": f"unknown lesson '{slug}'" }, 404 )
            return

        try:
            open_terminal( lesson )
        except ( OSError, RuntimeError ) as error:
            self._send_json( { "ok": False, "error": str( error ) }, 503 )
            return

        self._send_json( {
            "ok": True,
            "terminal": "Windows Terminal",
            "profile": WINDOWS_TERMINAL_PROFILE,
            "directory": str( lesson[ "directory" ] ),
        } )

    def _handle_run_update_status( self, parsed: urllib.parse.ParseResult ) -> None:
        item = urllib.parse.parse_qs( parsed.query ).get( "item", [ "" ] )[ 0 ]

        with _runs_lock:
            entry = _runs.get( item )
            if entry is None:
                self._send_json( { "running": False, "exit_code": None, "log_tail": "" } )
                return

            proc = entry[ "proc" ]
            exit_code = proc.poll()
            running = exit_code is None
            if not running and not entry[ "log_file" ].closed:
                entry[ "log_file" ].close()

        log_bytes = entry[ "log_path" ].read_bytes() if entry[ "log_path" ].exists() else b""
        log_tail = log_bytes[ -4000: ].decode( "utf-8", errors="replace" )

        self._send_json( { "running": running, "exit_code": exit_code, "log_tail": log_tail } )

    def _handle_git_status( self, parsed: urllib.parse.ParseResult ) -> None:
        item = urllib.parse.parse_qs( parsed.query ).get( "item", [ "" ] )[ 0 ]
        source = resolve_source_path( item ) if item else None

        exists = bool( source and source.exists() )
        git_dirty = False
        missing = False
        if exists:
            # catherine_agent_sdk/ is its own repo, separate from agent_blocks/
            # -- ask git to find the right root per file rather than assuming
            # REPO_ROOT, so this works no matter which repo the file lives in.
            toplevel = subprocess.run(
                [ "git", "-C", str( source.parent ), "rev-parse", "--show-toplevel" ],
                capture_output=True,
                text=True,
                check=False,
            )
            git_root = toplevel.stdout.strip()
            if git_root:
                result = subprocess.run(
                    [ "git", "-C", git_root, "status", "--porcelain", "--", str( source ) ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                git_dirty = bool( result.stdout.strip() )

            missing = docs_missing( DOCS_ROOT / item )

        self._send_json( {
            "item": item,
            "exists": exists,
            "dirty": git_dirty or missing,
            "docs_missing": missing,
        } )

    def log_message( self, format: str, *args ) -> None:
        pass


def main() -> None:
    import sys

    port = int( sys.argv[ 1 ] ) if len( sys.argv ) > 1 else 8931
    server = ThreadingHTTPServer( ( "", port ), DocsRequestHandler )
    print( f"Serving spa_documentation on http://localhost:{port} (Ctrl+C to stop)" )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
