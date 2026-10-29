"""The recorder service end to end over real sockets: a fake extension speaks
the WebSocket protocol, the control client speaks HTTP, SQLite is a temp file.

Pinned: the state machine refuses nonsense without changing anything; a web
page cannot open the socket or drive the control API; a secret the extension
leaks is still never stored; and a recording is saved, named, and listed.
"""
from __future__ import annotations

import asyncio
import json
import socket

import aiohttp
import pytest
from aiohttp import web

from browser_workflows.errors import IllegalTransition
from browser_workflows.kit import WorkflowKit
from browser_workflows.recording.guard import LoopbackRequestGuard
from browser_workflows.recording.session import StoredRecordingSession
from browser_workflows.storage.database import SqliteDatabase
from browser_workflows.storage.sessions import SqliteRecordingSessionStore
from browser_workflows.models import RawBrowserEvent, RecordingSessionInfo

EXT = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(( "127.0.0.1", 0 ))
        return s.getsockname()[ 1 ]


# -- state machine -----------------------------------------------------------

def test_illegal_transitions_change_nothing( tmp_path ):
    store   = SqliteRecordingSessionStore( SqliteDatabase( tmp_path / "db.sqlite" ))
    session = StoredRecordingSession( store )
    for bad in ( session.stop, session.finish ):
        with pytest.raises( IllegalTransition ):
            bad()
    session.begin( RecordingSessionInfo( id="s", state="recording" ))
    with pytest.raises( IllegalTransition ):
        session.begin( RecordingSessionInfo( id="t", state="recording" ))
    session.stop()
    with pytest.raises( IllegalTransition ):
        session.capture( RawBrowserEvent( kind="click" ))
    assert session.state == "saving" and session.info.id == "s"
    session.finish()
    assert session.state == "idle" and store.get( "s" ).state == "idle"


def test_restart_resumes_an_unsaved_session_as_saving( tmp_path ):
    store = SqliteRecordingSessionStore( SqliteDatabase( tmp_path / "db.sqlite" ))
    store.open( RecordingSessionInfo( id="old", state="recording" ))
    session = StoredRecordingSession( store )
    session.restore( store.latest_open())
    assert session.state == "saving"


def test_restart_can_save_a_legacy_background_click_without_changing_raw_events( tmp_path ):
    port = free_port()
    kit = WorkflowKit( db_path=tmp_path / "db.sqlite", port=port )
    store = kit.session_store()
    store.open( RecordingSessionInfo( id="legacy", state="saving", start_url="https://example.test/form" ))
    store.append( "legacy", RawBrowserEvent( kind="navigation", url="https://example.test/form", transition_type="typed" ))
    store.append( "legacy", RawBrowserEvent( kind="click", url="https://example.test/form", tag="body" ))
    before = [ e.model_dump() for e in store.events( "legacy" )]

    async def save() -> None:
        runner = web.AppRunner( kit.app())
        await runner.setup()
        await web.TCPSite( runner, "127.0.0.1", port ).start()
        try:
            async with aiohttp.ClientSession() as http:
                r = await http.post( f"http://127.0.0.1:{ port }/recording/save", json={ "name": "Background" })
                assert r.status == 200, await r.text()
                assert ( await r.json())[ "state" ] == "idle"
        finally:
            await runner.cleanup()

    asyncio.run( save())
    recording = kit.repository().by_name( "Background" )
    assert [ s.action_type for s in recording.steps ] == [ "navigate", "click" ]
    step = recording.steps[ 1 ]
    assert [( l.strategy, l.value ) for l in step.locators ] == [( "css", "body" )]
    assert step.metadata[ "element_key" ] == "css:body"
    assert [ e.model_dump() for e in store.events( "legacy" )] == before


# -- guard -------------------------------------------------------------------

@pytest.mark.parametrize( "host,origin,ctype,body,ok", [
    ( "127.0.0.1:8781", None, "application/json", True, True ),
    ( "localhost:8781", None, "", False, True ),
    ( "evil.example:8781", None, "application/json", True, False ),       # DNS rebinding
    ( "127.0.0.1:8781", "https://evil.example", "application/json", True, False ),
    ( "127.0.0.1:8781", None, "text/plain", True, False ),                 # a cross-site form's type
])
def test_control_guard( host, origin, ctype, body, ok ):
    guard = LoopbackRequestGuard( 8781, { EXT })
    assert ( guard.control_problem( host, origin, ctype, body ) is None ) is ok


def test_extension_guard_takes_only_our_origin():
    guard = LoopbackRequestGuard( 8781, { EXT })
    assert guard.extension_problem( "127.0.0.1:8781", EXT ) is None
    assert guard.extension_problem( "127.0.0.1:8781", "https://example.com" )
    assert guard.extension_problem( "127.0.0.1:8781", None )


# -- over the wire -----------------------------------------------------------

class FakeExtension:
    """Speaks the extension's half of the protocol, scripted."""

    def __init__( self, ws: aiohttp.ClientWebSocketResponse ) -> None:
        self.ws, self.session_id, self.states = ws, None, []

    async def serve( self ) -> None:
        async for msg in self.ws:
            data = json.loads( msg.data )
            if data[ "type" ] == "start_recording":
                self.session_id = data[ "session_id" ]
                await self.ws.send_json({ "type": "recording_started", "request_id": data[ "request_id" ],
                                          "session_id": self.session_id, "tab_id": 7, "url": "https://example.test/form",
                                          "title": "Form" })
            elif data[ "type" ] == "stop_recording" and "request_id" in data:
                await self.ws.send_json({ "type": "recording_stopped", "request_id": data[ "request_id" ]})
            elif data[ "type" ] == "state":
                self.states.append( data[ "state" ])

    async def send( self, kind: str, **event ) -> None:
        await self.ws.send_json({ "type": "event", "session_id": self.session_id, "event": { "kind": kind, **event }})


def test_record_stop_save_over_real_sockets( tmp_path ):
    asyncio.run( _record_stop_save( tmp_path ))


async def _record_stop_save( tmp_path ) -> None:
    port = free_port()
    kit  = WorkflowKit( db_path=tmp_path / "db.sqlite", port=port, extension_origins={ EXT },
                        log_path=tmp_path / "recorder.jsonl" )
    runner = web.AppRunner( kit.app())
    await runner.setup()
    await web.TCPSite( runner, "127.0.0.1", port ).start()
    base = f"http://127.0.0.1:{ port }"
    try:
        async with aiohttp.ClientSession() as http:
            # A web page may not connect, and may not drive the control API.
            with pytest.raises( aiohttp.WSServerHandshakeError ):
                await http.ws_connect( f"{ base }/ws", origin="https://evil.example" )
            r = await http.post( f"{ base }/recording/start", json={}, headers={ "Origin": "https://evil.example" })
            assert r.status == 403

            r = await http.post( f"{ base }/recording/start", json={})
            assert r.status == 503                      # no extension yet

            ws  = await http.ws_connect( f"{ base }/ws", origin=EXT )
            ext = FakeExtension( ws )
            server = asyncio.create_task( ext.serve())
            await ws.send_json({ "type": "hello", "state": "idle", "extension_version": "test" })

            r = await http.post( f"{ base }/recording/start", json={})
            body = await r.json()
            assert r.status == 200 and body[ "state" ] == "recording" and body[ "session" ][ "tab_id" ] == 7

            box = [{ "strategy": "css", "value": "#memo", "source": "id", "rank": 50 }]
            pw  = [{ "strategy": "css", "value": "#pw", "source": "id", "rank": 50 }]
            await ext.send( "navigation", url="https://example.test/form", transition_type="typed" )
            await ext.send( "text_committed", url="https://example.test/form", tag="input", input_type="text",
                            value="hello world", locators=box, field_hints={ "name": "memo" })
            # A buggy extension leaking a password: Python must still drop it.
            await ext.send( "text_committed", url="https://example.test/form", tag="input", input_type="password",
                            value="hunter2", locators=pw, field_hints={ "name": "pw" })
            await ext.send( "click", url="https://example.test/form", tag="button", text="Go",
                            locators=[{ "strategy": "css", "value": "#go", "source": "id", "rank": 50 }])
            await asyncio.sleep( 0.2 )

            r = await http.post( f"{ base }/recording/save", json={ "name": "too early" })
            assert r.status == 409                      # still recording

            r = await http.post( f"{ base }/recording/stop", json={})
            body = await r.json()
            assert body[ "state" ] == "saving" and body[ "session" ][ "event_count" ] == 4

            r = await http.post( f"{ base }/recording/save", json={ "name": "Fill Form" })
            body = await r.json()
            assert r.status == 200 and body[ "state" ] == "idle", body
            await asyncio.sleep( 0.1 )
            assert ext.states[ -1 ] == "idle"
            server.cancel()
            await ws.close()
    finally:
        await runner.cleanup()

    recording = kit.repository().by_name( "Fill Form" )
    assert [ s.action_type for s in recording.steps ] == [ "navigate", "enter_text", "enter_text", "click" ]
    assert recording.steps[ 1 ].value == "hello world"
    assert recording.steps[ 2 ].value is None and recording.steps[ 2 ].value_is_secret
    db_text = ( tmp_path / "db.sqlite" ).read_bytes()
    assert b"hunter2" not in db_text
    assert "hunter2" not in ( tmp_path / "recorder.jsonl" ).read_text()
