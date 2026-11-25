"""Failure Triage, offline: no post office, no mail, no network. Fixtures are the real error strings
seen on SQMC (tests/real_errors.jsonl), with no ids or tokens in them."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ADWS = Path( __file__ ).resolve().parents[ 2 ]
sys.path.insert( 0, str( ADWS ))

from failure_triage.corrections import CaseHistoryCorrections   # noqa: E402
from failure_triage.fingerprint import RuleFingerprinter          # noqa: E402
from failure_triage.interfaces import RouteRefused                # noqa: E402
from failure_triage.ledger import JsonClusterLedger               # noqa: E402
from failure_triage.models import Failure                         # noqa: E402
from failure_triage.owners import JsonOwnerTable                  # noqa: E402
from failure_triage.reconcile import counts, mark_fixed, reconcile   # noqa: E402
from failure_triage.router import PostOfficeRouter, YolandaNotifier  # noqa: E402
from failure_triage.sources import FailedMailboxSource            # noqa: E402
from failure_triage.status import TriageDesk, UnknownCluster      # noqa: E402
from failure_triage.triage import TriagePass                      # noqa: E402

NOW  = datetime( 2026, 11, 25, 12, 0, tzinfo=timezone.utc )
REAL = [ json.loads( l ) for l in ( Path( __file__ ).parent / "real_errors.jsonl" ).read_text().splitlines() if l.strip()]
FP, OWNERS = RuleFingerprinter(), JsonOwnerTable()


def failure( agent: str, error: str, at: datetime = NOW - timedelta( hours=1 ), n: int = 0 ) -> Failure:
    return Failure( task_id=f"t{ n }", agent=agent, sender="toyota", task_type="build", error=error, failed_at=at )


class NoSecrets:
    def found( self, text: str ) -> list[ str ]:
        return [ "a token" ] if "sk-live" in text else []


class FakeOutcome:
    def __init__( self, ok: bool, task_id: str ) -> None:
        self.ok, self.line, self.data = ok, "sent" if ok else "refused", ({ "task_id": task_id } if task_id else {})


class FakeOp:
    def __init__( self, sent: list[ dict ], ok: bool = True ) -> None:
        self.sent, self.ok = sent, ok

    def run( self, args: dict ) -> FakeOutcome:
        self.sent.append( args )
        return FakeOutcome( self.ok, f"po-{ len( self.sent ) }" if self.ok else "" )


def ops( sent: list[ dict ], ok: bool = True ):
    return lambda: { "send_and_capture": FakeOp( sent, ok )}


# -- fingerprint and owners -------------------------------------------------------------------------

def test_machine_ops_variants_are_one_cluster() -> None:
    keys = { FP.fingerprint( r[ "agent" ], r[ "error" ]) for r in REAL if r[ "agent" ] == "machine-ops" }
    assert len( keys ) == 1


def test_session_limit_reset_times_merge() -> None:
    a = "claude stopped: You've hit your session limit · resets 10:20pm (UTC)"
    b = "claude stopped: You've hit your session limit · resets 9:05am (UTC)"
    assert FP.fingerprint( "claude-sonnet", a ) == FP.fingerprint( "claude-sonnet", b )


def test_real_errors_make_a_small_number_of_clusters() -> None:
    keys = { FP.fingerprint( r[ "agent" ], r[ "error" ]) for r in REAL }
    assert 8 <= len( keys ) <= 18, len( keys )


def test_every_real_error_has_a_named_owner_rule() -> None:
    unmatched = [ r for r in REAL if OWNERS.match( r[ "agent" ], FP.normalise( r[ "error" ])).name == "unmatched" ]
    assert unmatched == [], unmatched[ :3 ]


@pytest.mark.parametrize( "needle,klass,owner", [
    ( "error_max_turns", "config", "sonnet" ),
    ( "Machine Ops refused", "code", "sonnet" ),
    ( "does not support this model", "environment", "eg" ),
    ( "session limit", "environment", "eg" ),
    ( "stream disconnected", "environment", "eg" ),
    ( "REFUSED", "person", "eg" ),
])
def test_owner_classes( needle: str, klass: str, owner: str ) -> None:
    row = next( r for r in REAL if needle in r[ "error" ])
    rule = OWNERS.match( row[ "agent" ], FP.normalise( row[ "error" ]))
    assert ( rule.klass, rule.owner ) == ( klass, owner ), rule.name


def test_unknown_error_goes_to_the_mechanic() -> None:
    rule = OWNERS.match( "newcomer", "something nobody has seen" )
    assert ( rule.name, rule.owner, rule.task_type ) == ( "unmatched", "mechanic", "diagnose" )


# -- reconcile --------------------------------------------------------------------------------------

def test_reconcile_is_idempotent() -> None:
    clusters: dict = {}
    fs = [ failure( "codex", "stream disconnected before completion", n=i ) for i in range( 3 )]
    first = reconcile( clusters, fs, FP, OWNERS, NOW )
    again = reconcile( clusters, fs, FP, OWNERS, NOW )
    assert [ c.kind for c in first ] == [ "new" ] and again == []
    assert next( iter( clusters.values())).count == 3


def test_old_failures_close_on_the_first_pass() -> None:
    clusters: dict = {}
    reconcile( clusters, [ failure( "codex", "gpt-6-luna is not a model", NOW - timedelta( days=9 ))], FP, OWNERS, NOW )
    ( cluster, ) = clusters.values()
    assert cluster.state == "closed" and cluster.mail_reason == ""


def test_a_failure_after_a_fix_is_a_regression() -> None:
    clusters: dict = {}
    err = "Machine Ops refused the request: ... Reason: unit is not approved."
    reconcile( clusters, [ failure( "machine-ops", err, NOW - timedelta( days=1 ))], FP, OWNERS, NOW )
    ( cluster, ) = clusters.values()
    mark_fixed( cluster, "7fa5717 unit optional", NOW )
    later = NOW + timedelta( hours=2 )
    changes = reconcile( clusters, [ failure( "machine-ops", err, NOW - timedelta( days=1 )),
                                     failure( "machine-ops", err, later, n=1 )], FP, OWNERS, later )
    assert [ c.kind for c in changes ] == [ "regressed" ]
    assert cluster.state == "open" and cluster.regressions == 1 and cluster.mail_reason.startswith( "regressed" )


def test_a_fix_with_a_quiet_spell_closes() -> None:
    clusters: dict = {}
    reconcile( clusters, [ failure( "claude-sonnet", "error_max_turns" )], FP, OWNERS, NOW )
    ( cluster, ) = clusters.values()
    mark_fixed( cluster, "abc123 more turns", NOW )
    reconcile( clusters, [ failure( "claude-sonnet", "error_max_turns" )], FP, OWNERS, NOW + timedelta( days=2 ))
    assert cluster.state == "fixed"
    reconcile( clusters, [ failure( "claude-sonnet", "error_max_turns" )], FP, OWNERS, NOW + timedelta( days=3, minutes=1 ))
    assert cluster.state == "closed" and counts( clusters )[ "live" ] == 0


def test_mail_owed_when_repeated_or_for_eg() -> None:
    clusters: dict = {}
    reconcile( clusters, [ failure( "claude-sonnet", "error_max_turns", n=i ) for i in range( 2 )]
                         + [ failure( "opus", "Claude Code 2.1.250 does not support this model" )], FP, OWNERS, NOW )
    reasons = sorted( c.mail_reason for c in clusters.values())
    assert reasons == [ "keeps happening", "needs you" ]


# -- router -----------------------------------------------------------------------------------------

def test_router_sends_a_plan_and_refuses_a_build() -> None:
    sent: list[ dict ] = []
    clusters: dict = {}
    reconcile( clusters, [ failure( "claude-sonnet", "error_max_turns" )], FP, OWNERS, NOW )
    ( cluster, ) = clusters.values()
    rule = OWNERS.rule( cluster.rule )
    router = PostOfficeRouter( ops( sent ), NoSecrets())
    assert router.send( cluster, rule ) == "po-1"
    assert sent[ 0 ][ "task_type" ] == "plan" and sent[ 0 ][ "sender" ] == "failure-triage"
    assert "Plan only: do not change any file" in sent[ 0 ][ "text" ]
    with pytest.raises( RouteRefused ):
        router.send( cluster, rule.model_copy( update={ "task_type": "build" }))


def test_router_withholds_text_that_looks_like_a_secret() -> None:
    sent: list[ dict ] = []
    clusters: dict = {}
    reconcile( clusters, [ failure( "claude-sonnet", "error_max_turns sk-live-abc" )], FP, OWNERS, NOW )
    ( cluster, ) = clusters.values()
    with pytest.raises( RouteRefused ):
        PostOfficeRouter( ops( sent ), NoSecrets()).send( cluster, OWNERS.rule( cluster.rule ))
    assert sent == []


def test_notifier_writes_one_message_to_yolanda() -> None:
    sent: list[ dict ] = []
    YolandaNotifier( ops( sent ), NoSecrets(), to="eg@example.test" ).digest( "[Triage] x", "body" )
    assert sent[ 0 ][ "recipient" ] == "yolanda" and "To: eg@example.test" in sent[ 0 ][ "text" ]


# -- the pass, end to end over files -------------------------------------------------------------------

def write_failed( root: Path, agent: str, error: str, n: int, at: datetime ) -> None:
    box = root / agent / "inbox" / "failed"
    box.mkdir( parents=True, exist_ok=True )
    ( box / f"{ n }.json" ).write_text( json.dumps({ "id": f"task-{ n }", "kind": "request", "sender": "chatgpt",
        "recipient": agent, "task_type": "build", "text": "do it", "error": error, "completed_at": at.isoformat()}))


def build( tmp: Path, sent: list[ dict ], dry: bool, ok: bool = True ) -> TriagePass:
    return TriagePass( [ FailedMailboxSource( tmp / "mailboxes" )], FP, JsonClusterLedger( tmp / "clusters.json" ), OWNERS,
                       PostOfficeRouter( ops( sent, ok ), NoSecrets()), YolandaNotifier( ops( sent, ok ), NoSecrets()),
                       dry_run=dry, clock=lambda: NOW )


def run( p: TriagePass ):
    p.collect(); p.reconcile(); p.route(); p.notify()
    return p.report()


def seed( tmp: Path ) -> None:
    for i in range( 3 ):
        write_failed( tmp / "mailboxes", "claude-sonnet", "error_max_turns", i, NOW - timedelta( hours=i + 1 ))
    write_failed( tmp / "mailboxes", "opus", "Claude Code 2.1.250 does not support this model", 9, NOW - timedelta( hours=2 ))
    write_failed( tmp / "mailboxes", "codex", "gpt-6-luna", 10, NOW - timedelta( days=20 ))


def test_dry_run_writes_and_sends_nothing( tmp_path: Path ) -> None:
    seed( tmp_path )
    sent: list[ dict ] = []
    report = run( build( tmp_path, sent, dry=True ))
    assert sent == [] and not ( tmp_path / "clusters.json" ).exists()
    assert report.line.startswith( "DRY RUN: 2 live" ) and report.mailed.startswith( "(dry run)" )


def test_live_pass_routes_mails_and_saves( tmp_path: Path ) -> None:
    seed( tmp_path )
    sent: list[ dict ] = []
    report = run( build( tmp_path, sent, dry=False ))
    assert report.ok and [ s[ "recipient" ] for s in sent ] == [ "sonnet", "yolanda" ]
    saved = JsonClusterLedger( tmp_path / "clusters.json" ).load()
    assert counts( saved ) == { "open": 1, "routed": 1, "fixed": 0, "closed": 1, "live": 2 }
    # a second pass changes nothing and mails no one
    sent.clear()
    again = run( build( tmp_path, sent, dry=False ))
    assert sent == [] and "this pass" not in again.line


def test_a_refused_route_is_reported_not_raised( tmp_path: Path ) -> None:
    seed( tmp_path )
    report = run( build( tmp_path, [], dry=False, ok=False ))
    assert not report.ok and "refused" in report.line


# -- the desk ---------------------------------------------------------------------------------------

def test_desk_records_a_fix_into_the_mechanics_history( tmp_path: Path ) -> None:
    seed( tmp_path )
    run( build( tmp_path, [], dry=False ))
    cases = tmp_path / "mechanic_cases.jsonl"
    desk = TriageDesk( JsonClusterLedger( tmp_path / "clusters.json" ), CaseHistoryCorrections( cases ), clock=lambda: NOW )
    line, data = desk.status()
    assert line.startswith( "2 live" )
    sonnet = next( c for c in data[ "live" ] if c[ "agent" ] == "claude-sonnet" )
    with pytest.raises( ValueError ):
        desk.record_fix( sonnet[ "id" ], "x" )
    with pytest.raises( UnknownCluster ):
        desk.record_fix( "zz", "abc123 something" )
    desk.record_fix( sonnet[ "id" ][ :6 ], "abc123 max_turns raised to 60" )
    case = json.loads( cases.read_text().splitlines()[ 0 ])
    assert case[ "correction" ] == "abc123 max_turns raised to 60" and case[ "recipient" ] == "claude-sonnet"
    sys.path.insert( 0, str( ADWS.parent / "lancedb_memory" ))
    from agent_comms.mechanic import JsonlCaseHistory
    assert "fixed by: abc123" in JsonlCaseHistory( cases ).similar( "error_max_turns" )[ 0 ]
