"""The dashboard desk: the same NDJSON turn contract Toyota and Sydney speak,
served on loopback only, and a debug Chrome started only when it's down."""
import json
import threading
import urllib.request
from pathlib import Path

import pytest

from browser_workflows.errors import BrowserWorkflowError
from cashy.kit import CashyKit
from cashy.launching import LaunchingConnection
from cashy.models import CashyReply
from cashy.presenting.desk import CashyDesk
from cashy.presenting.handoff import WordHandoffDetector
from cashy.presenting.sidecar import LOOPBACK, CashySidecar


class _Agent:
    def __init__( self ) -> None:
        self.heard: list[ tuple[ str, str ]] = []

    def turn( self, text: str, actor: str = "user", session_id: str = "default" ) -> CashyReply:
        self.heard.append(( text, actor ))
        if text == "boom":
            raise RuntimeError( "the page went away" )
        return CashyReply( lines=[ f"heard { text }" ], state="idle" )


class _Probe:
    def __init__( self, answers: list[ bool ]) -> None:
        self._answers = answers

    def reachable( self ) -> bool:
        return self._answers.pop( 0 ) if len( self._answers ) > 1 else self._answers[ 0 ]


def _desk( tmp_path: Path, agent: _Agent ) -> CashyDesk:
    kit = CashyKit( port=9, db_path=tmp_path / "db.sqlite", data_dir=tmp_path / "cashy", observers=[])
    return CashyDesk( agent, kit.sessions(), WordHandoffDetector(), _Probe([ False ]), port=9 )


@pytest.mark.parametrize( "said, agent", [
    ( "put Toyota on", "toyota" ), ( "let me talk to Sydney", "sydney" ), ( "switch to mazda please", "mazda" ),
    ( "what did Toyota spend", None ), ( "open Chase", None ),
])
def test_a_handoff_needs_a_verb_and_a_name( said: str, agent: str | None ) -> None:
    found = WordHandoffDetector().detect( said )
    assert ( found.agent if found else None ) == agent


def test_the_desk_speaks_the_receptionist_turn_records( tmp_path: Path ) -> None:
    agent = _Agent()
    desk  = _desk( tmp_path, agent )
    kinds = [ json.loads( r.model_dump_json())[ "kind" ] for r in desk.turn( "status" )]
    assert kinds == [ "reply", "result" ]
    assert agent.heard == [( "status", "user" )]                # the dashboard is the person
    handed = [ json.loads( r.model_dump_json()) for r in desk.turn( "put Sydney on" )]
    assert handed[ 0 ][ "handoff" ] == { "agent": "sydney", "name": "Sydney" }
    assert len( agent.heard ) == 1                               # a handoff never reaches Cashy
    assert [ json.loads( r.model_dump_json())[ "kind" ] for r in desk.turn( "boom" )] == [ "error" ]


def test_the_sidecar_round_trips_ndjson_on_loopback( tmp_path: Path ) -> None:
    server = CashySidecar( _desk( tmp_path, _Agent()), port=0 )
    assert server.server_address[ 0 ] == LOOPBACK
    worker = threading.Thread( target=server.serve_forever, daemon=True )
    worker.start()
    try:
        base = f"http://{ LOOPBACK }:{ server.server_address[ 1 ] }"
        health = json.loads( urllib.request.urlopen( f"{ base }/health", timeout=5 ).read())
        assert health[ "agent" ] == "cashy" and health[ "debug_chrome" ] is False
        request = urllib.request.Request( f"{ base }/turn", data=json.dumps({ "text": "status" }).encode(),
                                          headers={ "Content-Type": "application/json" }, method="POST" )
        records = [ json.loads( l ) for l in urllib.request.urlopen( request, timeout=5 ).read().splitlines() if l ]
        assert records[ 0 ] == { **records[ 0 ], "kind": "reply", "text": "heard status" }
        bad = urllib.request.Request( f"{ base }/turn", data=b'{"nope": 1}', method="POST" )
        with pytest.raises( urllib.error.HTTPError ) as refused:
            urllib.request.urlopen( bad, timeout=5 )
        assert refused.value.code == 400
    finally:
        server.shutdown()
        server.server_close()


class _Connection:
    def __init__( self, fails: int ) -> None:
        self.fails, self.calls = fails, 0

    def attach( self, target_id: str, hosts: list[ str ]):
        self.calls += 1
        if self.calls <= self.fails:
            raise BrowserWorkflowError( "nothing on port 9222" )
        return "browser"


class _Launcher:
    def __init__( self ) -> None:
        self.launched = 0

    def launch( self ) -> str:
        self.launched += 1
        return "opened"


def test_chrome_is_started_only_when_nothing_answers() -> None:
    launcher = _Launcher()
    down     = LaunchingConnection( _Connection( 1 ), _Probe([ False, False, True ]), launcher, sleep=lambda s: None )
    assert down.attach( "", []) == "browser" and launcher.launched == 1
    up = LaunchingConnection( _Connection( 1 ), _Probe([ True ]), launcher, sleep=lambda s: None )
    with pytest.raises( BrowserWorkflowError ):                  # Chrome is up: the failure is real
        up.attach( "", [])
    assert launcher.launched == 1
    never = LaunchingConnection( _Connection( 9 ), _Probe([ False ]), launcher, wait_seconds=1, sleep=lambda s: None )
    with pytest.raises( BrowserWorkflowError, match="couldn't start" ):
        never.attach( "", [])
