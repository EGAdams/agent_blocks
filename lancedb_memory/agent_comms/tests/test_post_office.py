"""The post office, end to end, offline.

Real mailboxes in a tmpdir, the real registry file, the real router,
validators, worker, board and mechanic. Only the runtimes are doubles: the
Claude SDK call (FakeRoleRunner stands in for run_role, so the AgentRole it
would run is inspected instead) and the receptionists' sidecars.

    cd lancedb_memory
    .venv/bin/python -m agent_comms.tests.test_post_office
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from agent_comms.adapters.launchers import RecordingLauncher
from agent_comms.executors.basic import HelloWorldExecutor, ScriptedExecutor
from agent_comms.executors.claude_sdk import ClaudeSdkExecutor
from agent_comms.executors.mechanic import DiagnosisExecutor
from agent_comms.executors.sidecar import SidecarTurnExecutor
from agent_comms.factory import build_post_office
from agent_comms.models import AgentCard, MessageKind, TaskRequest, TaskResult, TaskStatus
from agent_comms.state import IllegalTransition, advance
from agent_comms.tests.fakes import FakeClock, FakeRoleRunner


class Harness:
    """A post office in a tmpdir, with doubles for the SDK and the sidecars."""

    def __init__( self, role_runner: FakeRoleRunner | None = None ) -> None:
        self.dir         = Path( tempfile.mkdtemp( prefix="post_office_" ))
        self.clock       = FakeClock()
        self.launcher    = RecordingLauncher()
        self.role_runner = role_runner or FakeRoleRunner()
        self.sidecar     = ScriptedExecutor([ TaskResult( ok=True, text="Toyota here: we spent $1,204 on utilities.",
                                                          performer="sidecar:fake" )], performer="sidecar:fake" )
        holder = {}

        def claude_sdk( card: AgentCard ):
            s = card.settings
            return ClaudeSdkExecutor( card.display_name, s[ "model" ], s[ "tools" ], s[ "workdir" ],
                                      str( self.dir / "reports" ), role_runner=self.role_runner )

        builders = {
            "claude_sdk":  claude_sdk,
            "sidecar":     lambda card: self.sidecar,
            "mechanic":    lambda card: DiagnosisExecutor( holder[ "kit" ].diagnostic, holder[ "kit" ].cases ),
            "hello_world": lambda card: HelloWorldExecutor(),
        }
        self.kit = build_post_office( root=self.dir, launcher=self.launcher, clock=self.clock, builders=builders )
        holder[ "kit" ] = self.kit

    def work( self, agent: str ) -> list:
        return asyncio.run( self.kit.worker_for( agent ).drain())

    def work_while_waiting( self, agent: str ) -> None:
        """While a sender waits, the recipient's worker runs -- as a woken one would."""
        self.clock.on_sleep = lambda: self.work( agent )

    def close( self ) -> None:
        shutil.rmtree( self.dir, ignore_errors=True )


def run( test ) -> None:
    test()
    print( f"ok  { test.__name__ }" )


# -- 1. Sydney -> Sonnet build -----------------------------------------------

def test_sydney_build_request_reaches_sonnet_with_write_tools() -> None:
    h = Harness()
    try:
        request = h.kit.router.route( "sydney", "Ask Sonnet to replicate the Swarm-Forge communication system" )
        assert request is not None and request.recipient == "sonnet" and request.task_type == "build", request
        receipt = h.kit.dispatcher.send( request )
        assert receipt.accepted and receipt.task_id and not receipt.waited, receipt   # builds are asynchronous
        assert h.launcher.woken == [ "sonnet" ]
        h.work( "sonnet" )
        call = h.role_runner.calls[ 0 ]
        assert call[ "model" ] == "claude-sonnet-5"
        assert { "Write", "Edit", "Bash" } <= set( call[ "role" ].allowed_tools )
        assert "replicate the Swarm-Forge communication system" in call[ "role" ].task_prompt
        assert "Never push" in call[ "role" ].system_prompt
        reply = h.kit.replies.await_reply( "sydney", receipt.task_id, 0 )
        assert reply is not None and reply.outcome is TaskStatus.SUCCEEDED and "12 tests passed" in reply.text
        assert reply.performer == "claude_sdk:claude-sonnet-5"
    finally:
        h.close()


# -- 2. Sydney -> Opus delegation --------------------------------------------

def test_sydney_delegates_a_plan_to_opus() -> None:
    h = Harness()
    try:
        receipt = h.kit.dispatcher.send( h.kit.router.route( "sydney", "opus: design the agent post office rollout" ))
        assert receipt.accepted
        h.work( "opus" )
        assert h.role_runner.calls[ 0 ][ "model" ] == "claude-opus-5-5"
        assert "Change no source files" in h.role_runner.calls[ 0 ][ "role" ].task_prompt
        assert h.kit.board.task( receipt.task_id ).status is TaskStatus.SUCCEEDED
    finally:
        h.close()


# -- 3. Toyota through the common routing ------------------------------------

def test_toyota_and_sydney_talk_through_the_post_office() -> None:
    h = Harness()
    try:
        h.work_while_waiting( "toyota" )
        receipt = h.kit.dispatcher.send( h.kit.router.route( "sydney", "Ask Toyota what we spent on utilities?" ))
        assert receipt.accepted and receipt.waited and receipt.reply is not None, receipt   # questions wait
        assert "$1,204" in receipt.reply.text
        assert h.sidecar.seen[ 0 ].sender == "sydney" and h.sidecar.seen[ 0 ].task_type == "ask"
        back = h.kit.router.route( "toyota", "Sydney: the dashboard tab is broken, please have it looked at" )
        assert back.recipient == "sydney"
    finally:
        h.close()


def test_a_reply_cannot_loop_back_to_a_waiting_sender() -> None:
    h = Harness()
    try:
        receipt = h.kit.dispatcher.send( TaskRequest( sender="sydney", recipient="toyota", task_type="ask",
                                                      text="what did we spend?", wait_seconds=0 ))
        h.kit.transport.claim( "toyota", receipt.task_id )      # Toyota is working on it now
        loop = h.kit.dispatcher.send( TaskRequest( sender="toyota", recipient="sydney", task_type="ask",
                                                   text="what did Sydney want?" ))
        assert not loop.accepted and "loop" in loop.reason
        onward = h.kit.dispatcher.send( TaskRequest( sender="toyota", recipient="sonnet", task_type="ask",
                                                     text="where is the spend query?", wait_seconds=0 ))
        assert onward.accepted
        sent = h.kit.board.task( onward.task_id )
        assert sent.parent_id == receipt.task_id and sent.trace == [ "sydney", "toyota" ], sent
    finally:
        h.close()


# -- 4. The mechanic gets what it needs --------------------------------------

def test_failed_task_reaches_the_mechanic_with_a_diagnosis() -> None:
    h = Harness( FakeRoleRunner( text="pytest: 2 failed in test_router.py", is_error=True ))
    try:
        session = h.dir / "sessions" / "abcd1234"
        session.mkdir( parents=True )
        ( session / "events.jsonl" ).write_text( "\n".join( json.dumps( e ) for e in [
            { "type": "phase_end", "name": "request", "payload": { "status": "success" }},
            { "type": "phase_end", "name": "build",   "phase_id": "abcd1234_02_build", "payload": { "status": "failed" }},
        ]))
        h.kit.diagnostic._adw_sessions.append( h.dir / "sessions" )
        receipt = h.kit.dispatcher.send( TaskRequest( sender="sydney", recipient="sonnet", task_type="build",
                                                      text="add the router", context={ "adw_id": "abcd1234" }))
        h.work( "sonnet" )
        failed = h.kit.board.task( receipt.task_id )
        assert failed.status is TaskStatus.FAILED and failed.attempts == 1, failed   # a build is never re-run
        assert "mechanic" in h.launcher.woken
        done = h.work( "mechanic" )
        assert done and done[ 0 ].status is TaskStatus.SUCCEEDED, done
        diagnosis = h.kit.diagnostic.diagnose( receipt.task_id )
        assert diagnosis.failed_phase == "build" and diagnosis.performer == "claude_sdk:claude-sonnet-5"
        assert diagnosis.request_text == "add the router" and "2 failed" in diagnosis.error
        assert [ "task.sent", "task.claimed", "task.executing", "task.executed", "task.failed" ] == \
               [ e.name for e in diagnosis.events if e.name != "task.replied" ], [ e.name for e in diagnosis.events ]
        # The next identical failure is recognised as a known case.
        assert h.kit.cases.similar( diagnosis.error )
    finally:
        h.close()


# -- 5. Structured request/response ------------------------------------------

def test_request_and_reply_are_structured_and_correlated() -> None:
    h = Harness()
    try:
        h.work_while_waiting( "hello-world" )
        receipt = h.kit.dispatcher.send( TaskRequest( sender="toyota", recipient="hello-world",
                                                      task_type="hello", text="say hello" ))
        reply = receipt.reply
        assert reply.kind is MessageKind.REPLY and reply.correlation_id == receipt.task_id
        assert ( reply.sender, reply.recipient, reply.text ) == ( "hello-world", "toyota", "Hello World" )
        assert reply.status is TaskStatus.SUCCEEDED      # the reply itself was taken out of Toyota's inbox
        assert h.kit.replies.await_reply( "toyota", receipt.task_id, 0 ) is None   # never consumed twice
    finally:
        h.close()


# -- 6. Asynchronous delegation doesn't block --------------------------------

def test_async_send_returns_before_any_work_and_the_reply_arrives_later() -> None:
    h = Harness()
    try:
        receipt = h.kit.dispatcher.send( TaskRequest( sender="toyota", recipient="opus", task_type="review",
                                                      text="review the post office" ))
        assert receipt.accepted and not receipt.waited and h.role_runner.calls == []
        assert h.kit.board.task( receipt.task_id ).status is TaskStatus.QUEUED
        h.work( "opus" )
        assert [ t.status for t in h.kit.board.tasks_from( "toyota" )] == [ TaskStatus.SUCCEEDED ]
        assert h.kit.replies.await_reply( "toyota", receipt.task_id, 0 ).outcome is TaskStatus.SUCCEEDED
    finally:
        h.close()


def test_a_timed_out_wait_loses_nothing() -> None:
    h = Harness()
    try:
        receipt = h.kit.dispatcher.send( TaskRequest( sender="sydney", recipient="sonnet", task_type="ask",
                                                      text="where is the router?", wait_seconds=2 ))
        assert receipt.accepted and receipt.waited and receipt.reply is None
        h.work( "sonnet" )
        assert h.kit.replies.await_reply( "sydney", receipt.task_id, 0 ) is not None
        assert "task.wait_timed_out" in [ e.name for e in h.kit.event_log.events_for( receipt.task_id )]
    finally:
        h.close()


# -- 7. Swapping the implementation ------------------------------------------

def test_replacing_an_agents_executor_needs_no_change_to_the_caller() -> None:
    h = Harness()
    try:
        h.kit.overrides[ "sonnet" ] = ScriptedExecutor([ TaskResult( ok=True, text="done by the stand-in",
                                                                     performer="python:stand-in" )],
                                                       performer="python:stand-in" )
        receipt = h.kit.dispatcher.send( h.kit.router.route( "sydney", "sonnet: build the router" ))
        h.work( "sonnet" )
        reply = h.kit.replies.await_reply( "sydney", receipt.task_id, 0 )
        assert reply.text == "done by the stand-in" and reply.performer == "python:stand-in"
        assert h.role_runner.calls == []
    finally:
        h.close()


# -- 8. Failures are observable and actionable -------------------------------

def test_refusals_say_why_and_are_logged() -> None:
    h = Harness()
    try:
        nobody = h.kit.dispatcher.send( TaskRequest( sender="sydney", recipient="gemini", task_type="build", text="x" ))
        assert not nobody.accepted and "no agent called gemini" in nobody.reason
        cant = h.kit.dispatcher.send( TaskRequest( sender="sydney", recipient="mechanic", task_type="plan", text="x" ))
        assert not cant.accepted and "can do: diagnose, repair" in cant.reason, cant.reason
        stranger = h.kit.dispatcher.send( TaskRequest( sender="mallory", recipient="sonnet", task_type="build", text="x" ))
        assert not stranger.accepted and "isn't registered" in stranger.reason
        assert [ e.name for e in h.kit.event_log.all()] == [ "task.refused" ] * 3
    finally:
        h.close()


def test_idempotent_work_is_retried_and_a_crash_becomes_a_failure() -> None:
    h = Harness()
    try:
        flaky = ScriptedExecutor([ TaskResult( ok=False, text="rate limited", performer="x" ),
                                   TaskResult( ok=True, text="answer", performer="x" )], performer="x" )
        h.kit.overrides[ "sonnet" ] = flaky
        ask = h.kit.dispatcher.send( TaskRequest( sender="sydney", recipient="sonnet", task_type="ask",
                                                  text="where?", wait_seconds=0 ))
        h.work( "sonnet" )
        assert h.kit.board.task( ask.task_id ).status is TaskStatus.SUCCEEDED and len( flaky.seen ) == 2

        class Crashing:
            performer = "python:crash"

            async def execute( self, envelope ):
                raise RuntimeError( "boom" )

        h.kit.overrides[ "opus" ] = Crashing()
        build = h.kit.dispatcher.send( TaskRequest( sender="sydney", recipient="opus", task_type="build", text="x" ))
        h.work( "opus" )
        failed = h.kit.board.task( build.task_id )
        assert failed.status is TaskStatus.FAILED and failed.error == "RuntimeError: boom" and failed.attempts == 1
    finally:
        h.close()


def test_interrupted_work_is_recovered_without_rerunning_a_build() -> None:
    h = Harness()
    try:
        build = h.kit.dispatcher.send( TaskRequest( sender="sydney", recipient="sonnet", task_type="build", text="x" ))
        ask   = h.kit.dispatcher.send( TaskRequest( sender="sydney", recipient="sonnet", task_type="ask",
                                                    text="y", wait_seconds=0 ))
        h.kit.transport.claim( "sonnet", build.task_id )   # a worker claimed both, then died
        h.kit.transport.claim( "sonnet", ask.task_id )
        h.kit.worker_for( "sonnet" ).recover( older_than_seconds=-1 )
        assert h.kit.board.task( build.task_id ).status is TaskStatus.FAILED
        assert h.kit.board.task( ask.task_id ).status is TaskStatus.QUEUED
    finally:
        h.close()


def test_a_queued_task_can_be_cancelled_and_a_running_one_cannot() -> None:
    h = Harness()
    try:
        first  = h.kit.dispatcher.send( TaskRequest( sender="sydney", recipient="opus", task_type="plan", text="a" ))
        second = h.kit.dispatcher.send( TaskRequest( sender="sydney", recipient="opus", task_type="plan", text="b" ))
        h.kit.transport.claim( "opus", first.task_id )
        assert "already running" in h.kit.canceller.cancel( first.task_id, by="sydney" )
        assert "Cancelled" in h.kit.canceller.cancel( second.task_id, by="sydney" )
        assert h.kit.board.task( second.task_id ).status is TaskStatus.CANCELLED
    finally:
        h.close()


def test_lifecycle_refuses_illegal_moves() -> None:
    h = Harness()
    try:
        receipt  = h.kit.dispatcher.send( TaskRequest( sender="sydney", recipient="opus", task_type="plan", text="a" ))
        envelope = h.kit.board.task( receipt.task_id )
        try:
            advance( envelope, TaskStatus.SUCCEEDED )
        except IllegalTransition:
            pass
        else:
            raise AssertionError( "completed a task nobody claimed" )
    finally:
        h.close()


def test_mailbox_order_is_priority_then_time() -> None:
    h = Harness()
    try:
        low  = h.kit.dispatcher.send( TaskRequest( sender="sydney", recipient="opus", task_type="plan", text="a", priority=70 ))
        high = h.kit.dispatcher.send( TaskRequest( sender="sydney", recipient="opus", task_type="plan", text="b", priority=10 ))
        first = asyncio.run( h.kit.worker_for( "opus" ).work_one())
        assert first.id == high.task_id and low.task_id != high.task_id
    finally:
        h.close()


def test_sidecar_executor_reads_a_turn_stream() -> None:
    sidecar = SidecarTurnExecutor( "Toyota", "http://127.0.0.1:1", timeout_seconds=2 )
    lines   = [ json.dumps({ "kind": "acknowledgment", "text": "One moment." }),
                json.dumps({ "kind": "reply", "text": "We spent $40.", "cost_usd": 0.0 }),
                json.dumps({ "kind": "result" })]
    assert sidecar.result_from( lines ).text == "We spent $40."
    assert not sidecar.result_from([ json.dumps({ "kind": "error", "text": "groq 429" })]).ok
    unreachable = asyncio.run( sidecar.execute( _envelope()))
    assert not unreachable.ok and "could not be reached" in unreachable.text


def _envelope():
    from agent_comms.models import Envelope
    return Envelope( id="t1", kind=MessageKind.REQUEST, sender="sydney", recipient="toyota", task_type="ask",
                     text="hi", correlation_id="t1" )


def test_only_the_workers_reply_line_is_relayed() -> None:
    from agent_comms.executors.claude_sdk import reply_line
    assert reply_line( "Looking around.Now writing the report.\nREPLY: Built it; tests pass." ) == "Built it; tests pass."
    assert reply_line( "no marker here" ) == "no marker here"


def test_router_reads_addressees_and_verbs() -> None:
    h = Harness()
    try:
        r = h.kit.router
        assert r.route( "sydney", "Have the agent mechanic diagnose why the build failed" ).recipient == "mechanic"
        assert r.route( "sydney", "Have the agent mechanic diagnose why the build failed" ).task_type == "diagnose"
        assert r.route( "sydney", "to=opus type=review | look at worker.py" ).task_type == "review"
        assert r.route( "sydney", "what is the status of my tasks" ) is None   # nobody named: the caller's own board
        assert r.task_type( "what's the status of the build?" ) == "status"
        here = h.dir / "demo_repo"
        here.mkdir()
        assert r.route( "sydney", f"sonnet: build it in { here }." ).context == { "workdir": str( here )}
        assert r.route( "sydney", "have Opus fix the letta-code dashboard tab" ).context == \
               { "workdir": "/home/adamsl/letta-code/dashboard" }
        assert r.route( "sydney", "sonnet: build it in /no/such/dir" ).context == {}
        fix = h.kit.dispatcher.send( r.route( "sydney", "mechanic: fix Toyota's sentence splitter" ))
        assert h.kit.board.task( fix.task_id ).task_type == "repair"           # the card's own name for the work
    finally:
        h.close()


def test_browser_playback_reaches_the_recorder_adw_and_reports_its_verdict() -> None:
    """The `browser-recorder` card runs on the generic `adw` runtime: the request goes
    in as the first argument, and the ADW's closing DONE:/REFUSED: line is
    the reply. A stand-in script plays the ADW, so nothing opens Chrome."""
    from agent_comms.factory import default_builders
    h = Harness()
    try:
        request = h.kit.router.route( "toyota", "have the browser recorder run the pay bill playback" )
        assert request.recipient == "browser-recorder", request
        # A bare "browser" is not the recorder's name: this is still Sonnet's work.
        assert h.kit.router.route( "toyota", "the browser click ADW is broken, have Sonnet fix it" ).recipient == "sonnet"
        # A playback named with a build verb ("create") is still a playback.
        create = h.kit.dispatcher.send( h.kit.router.route( "toyota", "browser recorder: run create invoice" ))
        assert create.accepted, create
        script = h.dir / "fake_agent_request.py"
        script.write_text( "import sys\nprint( 'lifecycle noise' )\n"
                           "print( 'REFUSED: step 4 needs approval' if 'pay' in sys.argv[ 1 ] else 'DONE: ran it' )\n"
                           "print( 'more noise' )\n" )
        card  = h.kit.registry.card( "browser-recorder" ).model_copy( deep=True )
        card.settings.update({ "script": str( script ), "command": f"{ sys.executable } -I", "extra_args": []})
        runner = default_builders( h.kit.diagnostic, h.kit.cases, h.dir / "reports" )[ "adw" ]( card )
        h.kit.overrides[ "browser-recorder" ] = runner
        sent = h.kit.dispatcher.send( request )
        h.work( "browser-recorder" )
        task = h.kit.board.task( sent.task_id )
        assert task.task_type == "playback", task                            # the card's own name for the work
        assert task.status == TaskStatus.FAILED and "step 4 needs approval" in ( task.error or "" ), task
        assert runner._argv( task )[ -2: ] == [ "--actor", "toyota" ]                     # who asked, not the post office
    finally:
        h.close()


def test_a_bank_statement_request_reaches_the_statement_specialist() -> None:
    """Toyota naming a bank's statement reaches the `statement-specialist` card,
    whose ADW reads the month and runs that bank's templated playback."""
    h = Harness()
    try:
        for said in ( "get the October 2025 Chase statement", "ask the statement specialist for last month's chase statement" ):
            routed = h.kit.router.route( "toyota", said )
            assert routed.recipient == "statement-specialist", ( said, routed )
            sent = h.kit.dispatcher.send( routed )
            assert sent.accepted, sent
            assert h.kit.board.task( sent.task_id ).task_type == "statement"
        card = h.kit.registry.card( "statement-specialist" )
        assert card.settings[ "script" ] == "adws/adw_browser_statement_request.py"
        assert h.kit.router.route( "toyota", "have the browser recorder run the pay bill playback" ).recipient == "browser-recorder"
    finally:
        h.close()


def test_a_banking_request_reaches_cashy() -> None:
    """Sydney naming Cashy reaches the `cashy` card, whose ADW takes the request
    first and speaks as the asking agent -- who can never approve a payment."""
    h = Harness()
    try:
        for said in ( "ask cashy to open the transfer activity for the housing gas bill", "tell the banking agent to open Chase" ):
            routed = h.kit.router.route( "sydney", said )
            assert routed.recipient == "cashy", ( said, routed )
            sent = h.kit.dispatcher.send( routed )
            assert sent.accepted, sent
            assert h.kit.board.task( sent.task_id ).task_type == "banking"
        card = h.kit.registry.card( "cashy" )
        assert card.settings[ "script" ] == "adws/adw_cashy_request.py"
        assert card.settings[ "sender_flag" ] == "--actor"
        assert h.kit.router.route( "toyota", "get the October 2025 Chase statement" ).recipient == "statement-specialist"
    finally:
        h.close()


def test_a_timed_out_adw_is_ended_with_its_whole_process_group() -> None:
    """Behind `uv run` the ADW is a grandchild; the timeout must end it too."""
    from agent_comms.executors.mechanic import AdwSubprocessExecutor
    from agent_comms.models import Envelope
    import os, time
    d = Path( tempfile.mkdtemp( prefix="adw_timeout_" ))
    try:
        pid_file = d / "child.pid"
        script   = d / "slow.py"
        script.write_text( "import subprocess, sys\n"
                           "child = subprocess.Popen([ sys.executable, '-c', "
                           f"\"import os, signal, time; signal.signal( signal.SIGTERM, signal.SIG_IGN ); "
                           f"open( { str( pid_file )!r }, 'w' ).write( str( os.getpid() )); time.sleep( 60 )\" ], "
                           "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL )\n"
                           "child.wait()\n" )
        runner = AdwSubprocessExecutor( script, cwd=d, timeout_seconds=1.0 )
        result = asyncio.run( runner.execute( Envelope( id="t1", correlation_id="t1", kind=MessageKind.REQUEST, sender="toyota", recipient="browser-recorder",
                                                        task_type="playback", text="run it" )))
        assert not result.ok and "did not finish" in result.text, result
        child = int( pid_file.read_text())
        time.sleep( 0.2 )
        try:
            os.kill( child, 0 )
            alive = open( f"/proc/{ child }/stat" ).read().split()[ 2 ] != "Z"
        except ProcessLookupError:
            alive = False
        assert not alive, "the grandchild outlived the timeout"
    finally:
        shutil.rmtree( d, ignore_errors=True )


TESTS = [ v for k, v in dict( globals()).items() if k.startswith( "test_" )]

if __name__ == "__main__":
    for test in TESTS:
        run( test )
    print( f"{ len( TESTS ) } passed" )
