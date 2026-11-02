"""ScannerAgent, the device_control policy, and the loopback façade client, offline.

    cd lancedb_memory
    .venv/bin/python -m agent_bridge_worker.tests.test_scanner_agent
"""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from agent_bridge_worker.adapters.http_gateway import HttpTaskGateway
from agent_bridge_worker.adapters.http_scan_facade import HttpScanFacade, load_scan_token
from agent_bridge_worker.models import ClaimedTask, OutcomeKind, ScanPage, ScanRunSettings
from agent_bridge_worker.policy import FixedFirstWorkspaces, ScopedPolicy, StaticWorkspaces
from agent_bridge_worker.ports import GatewayError
from agent_bridge_worker.scanner_agent import ScannerAgent, clip_utf8
from agent_bridge_worker.tests.test_bridge_worker import LEASE, TASK_ID
from agent_comms.tests.fakes import FakeClock

SCAN_ID  = "scan_01ARZ3NDEKTSV4RRFFQ69G5FAV"
ORIGINAL = b"\xff\xd8\xff original bytes"


def task( prompt: str = "scanner=window", **over ) -> ClaimedTask:
    fields = dict( task_id=TASK_ID, lease_token=LEASE, lease_expires_at=0, lease_seconds=120, attempt=1,
                   agent="scanner", workspace="scanners", operation="scan", approval_mode="device_control",
                   prompt=prompt )
    fields.update( over )
    return ClaimedTask( **fields )


class FakeFacade:
    def __init__( self, statuses: list[ str ] | None = None, pages: int = 1, text: str = "TOTAL 12.34",
                  mazda: list[ dict ] | None = None, scala: list[ dict ] | None = None ) -> None:
        self.statuses = list( statuses or [ "scanning", "captured" ])
        self.pages    = pages
        self.text_    = text
        self.verdicts = list( mazda or [{ "status": "pending" }, { "status": "completed", "outcome": "pass" }])
        self.filings  = list( scala or [{}])                       # {} is an older façade that has no Scala
        self.started: list[ tuple[ str, str ]] = []
        self.start_error: GatewayError | None = None
        self.original_bytes = ORIGINAL
        self.width = 1600

    def _record( self ) -> dict:
        return { "scan_id": SCAN_ID, "scanner": "window", "scanner_name": "Window Scanner",
                 "status": self.statuses[ 0 ], "error_code": "device_offline" if self.statuses[ 0 ] == "failed" else None }

    def start( self, scanner: str, idempotency_key: str ) -> dict:
        if self.start_error is not None:
            raise self.start_error
        self.started.append(( scanner, idempotency_key ))
        return { **self._record(), "status": "queued", "replayed": False }

    def status( self, scan_id: str ) -> dict:
        record = self._record()
        if len( self.statuses ) > 1:
            self.statuses.pop( 0 )
        return record

    def document( self, scan_id: str ) -> dict:
        return { "scan_id": scan_id, "mime_type": "image/jpeg", "page_count": self.pages,
                 "sha256": hashlib.sha256( ORIGINAL ).hexdigest()}

    def original( self, scan_id: str ) -> bytes:
        return self.original_bytes

    def page( self, scan_id: str, page: int, rendition: str ) -> ScanPage:
        return ScanPage( jpeg=f"{ page }:{ rendition }".encode(), width=self.width, height=1200 )

    def text( self, scan_id: str, page: int ) -> dict:
        return { "page": page, "source": "local_ocr", "text": self.text_ }

    def mazda( self, scan_id: str ) -> dict:
        verdict = self.verdicts[ 0 ]
        if len( self.verdicts ) > 1:
            self.verdicts.pop( 0 )
        return verdict

    def outcomes( self, scan_id: str ) -> dict:
        filing = self.filings[ 0 ]
        if len( self.filings ) > 1:
            self.filings.pop( 0 )
        return { "scala": filing, "mazda": self.mazda( scan_id )}


class FakeUploader:
    def __init__( self, too_large: set[ bytes ] | None = None, manifest_error: GatewayError | None = None ) -> None:
        self.blobs: dict[ str, bytes ] = {}
        self.manifests: list[ dict ] = []
        self.too_large = too_large or set()
        self.manifest_error = manifest_error

    def upload_blob( self, scan_id: str, sha256: str, data: bytes ) -> None:
        assert hashlib.sha256( data ).hexdigest() == sha256
        if data in self.too_large:
            raise GatewayError( 413, "too_large", "blob too large" )
        self.blobs[ sha256 ] = data

    def post_scan( self, scan_id: str, manifest: dict ) -> dict:
        if self.manifest_error is not None:
            raise self.manifest_error
        self.manifests.append( manifest )
        return { "scan_id": scan_id }


def agent( facade: FakeFacade, uploader: FakeUploader, clock: FakeClock | None = None, **settings ) -> ScannerAgent:
    return ScannerAgent( facade, uploader, clock or FakeClock(), ScanRunSettings( **settings ))


def go() -> str:
    return "continue"


# -- ScannerAgent ---------------------------------------------------------------------

def test_a_scan_is_started_once_followed_uploaded_and_judged() -> None:
    facade, up = FakeFacade( pages=2 ), FakeUploader()
    out = agent( facade, up ).run( task(), Path( "/nonexistent" ), go )
    assert out.kind is OutcomeKind.SUCCEEDED, out
    assert facade.started == [( "window", TASK_ID )]           # keyed by the task: a retry cannot scan twice
    assert hashlib.sha256( ORIGINAL ).hexdigest() in up.blobs
    last = up.manifests[ -1 ]
    assert last[ "task_id" ] == TASK_ID and last[ "lease_token" ] == LEASE
    assert last[ "original" ][ "stored" ] is True
    assert sorted(( p[ "page" ], p[ "rendition" ]) for p in last[ "pages" ]) == [( 1, "full" ), ( 1, "preview" ), ( 2, "full" ), ( 2, "preview" )]
    assert [ t[ "text" ] for t in last[ "texts" ]] == [ "TOTAL 12.34", "TOTAL 12.34" ]
    assert last[ "record" ][ "mazda" ] == { "status": "completed", "outcome": "pass" }
    assert "replayed" not in last[ "record" ]
    assert len( up.manifests ) == 2                             # once at capture, again when the verdict changed
    assert out.details == { "scan_id": SCAN_ID, "scanner": "window", "status": "captured", "page_count": 2,
                            "scala_status": "unknown", "mazda_status": "completed" }
    assert "TOTAL 12.34" not in out.body and "TOTAL 12.34" not in out.summary   # no document contents in the result
    assert out.retryable is False and out.performer == "scanner"


def test_only_the_two_scanner_prompts_are_accepted() -> None:
    for prompt in ( "scanner=printer", "scanner=window; rm -rf /", "scan the window", "scanner=Window" ):
        facade = FakeFacade()
        out = agent( facade, FakeUploader()).run( task( prompt ), Path( "." ), go )
        assert out.kind is OutcomeKind.REFUSED, prompt
        assert facade.started == []


def test_a_refused_start_is_a_failure_that_is_never_retried() -> None:
    for status, code in (( 409, "scanner_busy" ), ( 429, "rate_limited" ), ( 403, "bridge_scans_disabled" ), ( 0, "unreachable" )):
        facade = FakeFacade()
        facade.start_error = GatewayError( status, code, "no" )
        up  = FakeUploader()
        out = agent( facade, up ).run( task(), Path( "." ), go )
        assert out.kind is OutcomeKind.FAILED and code in out.summary and out.body
        assert out.retryable is False and out.details is None and up.manifests == []


def test_a_hardware_failure_is_still_recorded_but_has_nothing_to_read() -> None:
    facade, up = FakeFacade( statuses=[ "scanning", "failed" ]), FakeUploader()
    out = agent( facade, up ).run( task(), Path( "." ), go )
    assert out.kind is OutcomeKind.FAILED and "device_offline" in out.summary
    assert len( up.manifests ) == 1 and up.manifests[ 0 ][ "record" ][ "status" ] == "failed"
    assert up.manifests[ 0 ][ "original" ] == {} and up.blobs == {}


def test_a_scan_that_never_finishes_gives_up_at_the_deadline() -> None:
    clock = FakeClock()
    facade, up = FakeFacade( statuses=[ "scanning" ]), FakeUploader()
    out = agent( facade, up, clock, capture_deadline=30 ).run( task(), Path( "." ), go )
    assert out.kind is OutcomeKind.FAILED and "scanning" in out.body
    assert 30 <= clock.now <= 33


def test_cancel_stops_the_wait_not_the_scanner() -> None:
    out = agent( FakeFacade( statuses=[ "scanning" ]), FakeUploader()).run( task(), Path( "." ), lambda: "cancel" )
    assert out.kind is OutcomeKind.CANCELLED and "still finishes" in out.summary


def test_a_lost_lease_reports_nothing() -> None:
    out = agent( FakeFacade(), FakeUploader()).run( task(), Path( "." ), lambda: "lost" )
    assert out.kind is OutcomeKind.LEASE_LOST
    up = FakeUploader( manifest_error=GatewayError( 409, "lease_invalid", "gone" ))
    assert agent( FakeFacade(), up ).run( task(), Path( "." ), go ).kind is OutcomeKind.LEASE_LOST


def test_an_original_too_large_for_the_gateway_still_sends_its_pages() -> None:
    up  = FakeUploader( too_large={ ORIGINAL, b"1:full" })
    out = agent( FakeFacade(), up ).run( task(), Path( "." ), go )
    assert out.kind is OutcomeKind.SUCCEEDED
    last = up.manifests[ -1 ]
    assert last[ "original" ][ "stored" ] is False
    assert [( p[ "page" ], p[ "rendition" ]) for p in last[ "pages" ]] == [( 1, "preview" )]
    assert "too large" in out.body


def test_an_original_that_does_not_match_its_record_is_not_sent() -> None:
    facade = FakeFacade()
    facade.original_bytes = b"something else"
    up  = FakeUploader()
    out = agent( facade, up ).run( task(), Path( "." ), go )
    assert out.kind is OutcomeKind.FAILED and "artifact_mismatch" in out.summary and up.blobs == {}


def test_a_page_without_a_size_fails_cleanly() -> None:
    facade = FakeFacade()
    facade.width = 0
    out = agent( facade, FakeUploader()).run( task(), Path( "." ), go )
    assert out.kind is OutcomeKind.FAILED


def test_text_is_clipped_per_page_and_in_total() -> None:
    up  = FakeUploader()
    out = agent( FakeFacade( pages=3, text="é" * 100 ), up, page_text_bytes=50, text_bytes=120 ).run( task(), Path( "." ), go )
    sizes = [ len( t[ "text" ].encode()) for t in up.manifests[ -1 ][ "texts" ]]
    assert sizes == [ 50, 50, 20 ] and "cut short" in out.body
    assert clip_utf8( "a\ud800b", 10 ) == "a�b"
    assert clip_utf8( "éé", 3 ) == "é"


def test_only_the_first_max_pages_are_sent() -> None:
    up  = FakeUploader()
    out = agent( FakeFacade( pages=5 ), up, max_pages=2 ).run( task(), Path( "." ), go )
    assert { p[ "page" ] for p in up.manifests[ -1 ][ "pages" ]} == { 1, 2 } and "first 2 of 5" in out.body


def test_mazda_that_never_answers_ends_at_its_deadline() -> None:
    clock = FakeClock()
    up    = FakeUploader()
    out   = agent( FakeFacade( mazda=[{ "status": "running" }]), up, clock, mazda_deadline=60 ).run( task(), Path( "." ), go )
    assert out.kind is OutcomeKind.SUCCEEDED and out.details[ "mazda_status" ] == "running"
    assert clock.now <= 60 + 15 + 3


def test_scala_files_a_non_finance_page_without_waiting_for_mazda() -> None:
    clock  = FakeClock()
    up     = FakeUploader()
    filed  = { "status": "filed", "folder": "documents_511/taxes", "method": "haiku", "confidence": 0.9,
               "reason": "A city income tax booklet" }
    facade = FakeFacade( mazda=[{ "status": "running" }], scala=[{ "status": "pending" }, filed ])
    out    = agent( facade, up, clock ).run( task(), Path( "." ), go )
    assert out.kind is OutcomeKind.SUCCEEDED
    assert out.summary.endswith( "Scala filed it in documents_511/taxes" ), out.summary
    assert out.details[ "scala_status" ] == "filed" and out.details[ "mazda_status" ] == "running"
    assert "untrusted" in out.body and "A city income tax booklet" in out.body
    assert up.manifests[ -1 ][ "record" ][ "scala" ] == filed
    assert clock.now < 60                                       # it did not sit out Mazda's 960 s


def test_scala_handing_a_page_to_mazda_waits_for_mazda() -> None:
    facade = FakeFacade( mazda=[{ "status": "running" }, { "status": "completed", "outcome": "pass" }],
                         scala=[{ "status": "handed_to_mazda" }])
    out    = agent( facade, FakeUploader()).run( task(), Path( "." ), go )
    assert out.details[ "mazda_status" ] == "completed"
    assert "Scala handed it to Mazda as a finance document; Mazda completed (pass)" in out.summary


def test_scalas_inbox_says_she_asked_eg() -> None:
    out = agent( FakeFacade( scala=[{ "status": "inbox", "folder": None }]), FakeUploader()).run( task(), Path( "." ), go )
    assert "in her inbox and she asked EG" in out.summary


def test_a_record_too_large_trims_both_outcomes() -> None:
    up  = FakeUploader()
    big = { "status": "filed", "reason": "x" * 40000 }
    agent( FakeFacade( scala=[ big ]), up ).run( task(), Path( "." ), go )
    sent = up.manifests[ -1 ][ "record" ]
    assert sent[ "scala" ][ "status" ] == "filed" and "too large" in sent[ "scala" ][ "warnings" ][ 0 ]
    assert "too large" in sent[ "mazda" ][ "warnings" ][ 0 ]


# -- policy -------------------------------------------------------------------------------

def test_scans_run_only_in_device_control_mode() -> None:
    caps   = { "scanner": { "scan": False }, "codex": { "inspect": False }}
    policy = ScopedPolicy( caps, frozenset(), frozenset({ "read_only", "workspace_write", "device_control" }))
    assert policy.refusal( task()) is None
    assert "device_control" in policy.refusal( task( approval_mode="read_only" ))
    assert policy.refusal( task( agent="codex" )) is not None
    off = ScopedPolicy( caps, frozenset())                         # the default modes leave scanning off
    assert "not enabled" in off.refusal( task())


def test_fixed_workspaces_come_first() -> None:
    base = Path( tempfile.mkdtemp())
    ( base / "scans" ).mkdir()
    ( base / "other" ).mkdir()
    resolver = FixedFirstWorkspaces({ "scanners": base / "scans" }, StaticWorkspaces({ "letta_code": base / "other" }))
    assert resolver.resolve( "scanners" ) == ( base / "scans" ).resolve()
    assert resolver.resolve( "letta_code" ) == ( base / "other" ).resolve()


# -- adapters -----------------------------------------------------------------------------

def test_the_facade_is_reached_on_loopback_only() -> None:
    for bad in ( "http://10.0.0.5:8765/api", "https://example.com/", "http://127.0.0.1.evil.com/", "file:///etc" ):
        try:
            HttpScanFacade( bad, "x" * 32 )
            raise AssertionError( bad )
        except ValueError:
            pass
    facade = HttpScanFacade( "http://127.0.0.1:8765/api/scan-bridge/v1", "secret-token-value-123456789" )
    assert "secret" not in repr( facade )
    try:
        facade.page( SCAN_ID, 1, "../../etc" )
        raise AssertionError( "a bad rendition was sent" )
    except ValueError:
        pass
    try:
        facade.status( "../scans" )
        raise AssertionError( "a bad id was sent" )
    except ValueError:
        pass


def test_a_token_file_others_can_read_is_refused() -> None:
    path = Path( tempfile.mkdtemp()) / "worker_token"
    path.write_text( "t" * 40 )
    os.chmod( path, 0o644 )
    try:
        load_scan_token( path )
        raise AssertionError( "a readable token was used" )
    except RuntimeError:
        pass
    os.chmod( path, 0o600 )
    assert load_scan_token( path ) == "t" * 40


def test_blob_uploads_take_only_well_formed_ids() -> None:
    gateway = HttpTaskGateway.__new__( HttpTaskGateway )         # no network: validation runs before any call
    for scan_id, sha in (( "scan_bad", "a" * 64 ), ( SCAN_ID, "../x" ), ( SCAN_ID, "A" * 64 )):
        try:
            gateway.upload_blob( scan_id, sha, b"x" )
            raise AssertionError(( scan_id, sha ))
        except ValueError:
            pass


TESTS = [ v for k, v in dict( globals()).items() if k.startswith( "test_" )]

if __name__ == "__main__":
    for test in TESTS:
        test()
        print( "ok", test.__name__ )
    print( f"{ len( TESTS )} passed" )
