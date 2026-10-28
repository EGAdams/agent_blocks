"""The whole cycle in a real Chrome: the extension records a form being filled
in, the service saves it as a named playback, and playback fills it again.

Opt-in (it starts Chrome for Testing headless and takes ~20s):

    BW_E2E=1 uv run --quiet --with pydantic --with pytest --with aiohttp --with selenium \\
        pytest -q browser_workflows/tests/test_e2e_chrome.py

Pinned: one EnterText per completed entry (not per keystroke); the password is
recorded as a secret with no value and appears nowhere on disk; the select,
checkbox and button become steps with locators; and replaying the saved
playback -- with a person supplying the password -- lands on the same page.
It uses the real port 8781, so the recorder service must not be running.
"""
from __future__ import annotations

import asyncio
import functools
import http.server
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

CFT_ROOT   = Path.home() / ".cache" / "selenium"
CHROME     = next( iter( sorted(( CFT_ROOT / "chrome" / "linux64" ).glob( "*/chrome" ))), None ) if CFT_ROOT.exists() else None
DRIVER     = CFT_ROOT / "chromedriver" / "linux64" / CHROME.parent.name / "chromedriver" if CHROME else None
EXTENSION  = Path( __file__ ).resolve().parents[ 1 ] / "chrome_extension"
PASSWORD   = "e2e-Secret-77"

pytestmark = pytest.mark.skipif( not os.environ.get( "BW_E2E" ) or CHROME is None,
                                 reason="set BW_E2E=1 (needs Chrome for Testing in ~/.cache/selenium)" )

FORM = """<!doctype html><title>E2E Form</title>
<form action="/done.html" method="get" onsubmit="return true">
  <label for="memo">Memo</label> <input id="memo" name="memo">
  <label for="pw">Password</label> <input id="pw" name="pw" type="password">
  <select id="kind" name="kind"><option value="a">Alpha</option><option value="b">Beta</option></select>
  <label><input id="agree" type="checkbox" name="agree"> I agree</label>
  <button id="go" type="button" onclick="location.href='/done.html'">Continue</button>
</form>"""
DONE = "<!doctype html><title>Done</title><p>All done</p>"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(( "127.0.0.1", 0 ))
        return s.getsockname()[ 1 ]


def port_free( port: int ) -> bool:
    with socket.socket() as s:
        return s.connect_ex(( "127.0.0.1", port )) != 0


@pytest.fixture
def site( tmp_path ):
    root = tmp_path / "site"
    root.mkdir()
    ( root / "form.html" ).write_text( FORM )
    ( root / "done.html" ).write_text( DONE )
    port    = free_port()
    handler = functools.partial( http.server.SimpleHTTPRequestHandler, directory=str( root ))
    server  = http.server.ThreadingHTTPServer(( "127.0.0.1", port ), handler )
    threading.Thread( target=server.serve_forever, daemon=True ).start()
    yield f"http://127.0.0.1:{ port }"
    server.shutdown()


@pytest.fixture
def kit( tmp_path ):
    from browser_workflows.kit import DEFAULT_PORT, WorkflowKit
    if not port_free( DEFAULT_PORT ):
        pytest.skip( "the recorder service is already running on 8781; stop it first" )
    kit  = WorkflowKit( db_path=tmp_path / "db.sqlite", log_path=tmp_path / "logs" / "recorder.jsonl" )
    loop = asyncio.new_event_loop()
    from aiohttp import web
    runner = web.AppRunner( kit.app())

    async def up() -> None:
        await runner.setup()
        await web.TCPSite( runner, "127.0.0.1", DEFAULT_PORT ).start()
    threading.Thread( target=loop.run_forever, daemon=True ).start()
    asyncio.run_coroutine_threadsafe( up(), loop ).result( 10 )
    yield kit
    asyncio.run_coroutine_threadsafe( runner.cleanup(), loop ).result( 10 )
    loop.call_soon_threadsafe( loop.stop )


@pytest.fixture
def chrome( tmp_path ):
    port = free_port()
    proc = subprocess.Popen([ str( CHROME ), "--headless=new", f"--remote-debugging-port={ port }",
                              f"--user-data-dir={ tmp_path / 'profile' }", f"--load-extension={ EXTENSION }",
                              f"--disable-extensions-except={ EXTENSION }", "--no-first-run", "--no-default-browser-check",
                              "--disable-gpu", "about:blank" ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL )
    yield port
    proc.terminate()
    try:
        proc.wait( 10 )
    except subprocess.TimeoutExpired:
        proc.kill()
    shutil.rmtree( tmp_path / "profile", ignore_errors=True )


def driver_for( port: int ):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    options = Options()
    options.debugger_address = f"127.0.0.1:{ port }"
    return webdriver.Chrome( options=options, service=Service( str( DRIVER )))


def until( check, seconds: float = 15.0, what: str = "" ):
    deadline = time.time() + seconds
    while time.time() < deadline:
        value = check()
        if value:
            return value
        time.sleep( 0.2 )
    raise AssertionError( f"timed out waiting for { what }" )


def test_record_save_and_replay_in_real_chrome( site, kit, chrome, tmp_path, monkeypatch ):
    monkeypatch.setenv( "SE_CHROMEDRIVER", str( DRIVER ))   # the kit's own attach must match Chrome 152 too
    from selenium.webdriver.common.by import By

    client = kit.recorder_client()
    until( lambda: ( client.status() or None ) and client.status().extension_connected, 20, "the extension to connect" )

    driver = driver_for( chrome )
    driver.get( f"{ site }/form.html" )
    until( lambda: driver.execute_script( "return document.readyState" ) == "complete", what="the form" )

    started = client.start()
    assert started.state == "recording", started.message
    time.sleep( 0.5 )                                     # capture script injected
    memo = driver.find_element( By.ID, "memo" )
    memo.send_keys( "Rent for May" )
    driver.find_element( By.ID, "pw" ).send_keys( PASSWORD )
    # Select.select_by_value clicks the <option> through chromedriver's JS atoms,
    # an untrusted change capture rightly ignores; a key press is trusted, as a person's is.
    driver.find_element( By.ID, "kind" ).send_keys( "B" )
    driver.find_element( By.ID, "agree" ).click()
    driver.find_element( By.ID, "go" ).click()
    until( lambda: driver.current_url.endswith( "/done.html" ), what="the done page" )
    time.sleep( 1.0 )                                     # outbox flushed

    stopped = client.stop()
    assert stopped.state == "saving", stopped.message
    saved = client.save( "E2E Form" )
    assert saved.state == "idle", saved.message

    recording = kit.repository().by_name( "E2E Form" )
    kinds     = [ s.action_type for s in recording.steps ]
    texts     = [ s for s in recording.steps if s.action_type == "enter_text" ]
    assert kinds.count( "enter_text" ) == 2, kinds        # one per entry, not per keystroke
    assert texts[ 0 ].value == "Rent for May"
    assert texts[ 1 ].value is None and texts[ 1 ].value_is_secret
    assert "select_option" in kinds and "set_checked" in kinds and "click" in kinds, kinds
    for path in tmp_path.rglob( "*" ):
        if path.is_file() and "profile" not in path.parts:
            assert PASSWORD.encode() not in path.read_bytes(), path

    # -- replay it ---------------------------------------------------------
    driver.get( f"{ site }/form.html" )
    driver.service.stop()                                 # detach; Chrome stays up

    from browser_workflows.operations.catalog import operations
    from .fakes import ScriptedConsole
    console = ScriptedConsole( answers=[ "YES" ] * 5, secrets=[ PASSWORD ])
    out = operations( kit, console )[ "execute_playback" ].run({ "name": "E2E Form", "port": chrome, "interactive": True })
    assert out.ok, out.line + "\n" + "\n".join( f"{ r.position } { r.action_type } { r.status } { r.message }"
                                                for r in kit.run_log().run( out.data[ "run_id" ]).results )

    check = driver_for( chrome )
    assert check.current_url.endswith( "/done.html" )
    check.service.stop()
