"""BrowserAgent and its uv command adapter, offline. No browser starts.

    cd lancedb_memory
    .venv/bin/python -m agent_bridge_worker.tests.test_browser_agent
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from agent_bridge_worker.adapters.browser_process import UvBrowserCommands
from agent_bridge_worker.browser_agent import BrowserAgent, BrowserCommandFailed
from agent_bridge_worker.models import ClaimedTask, OutcomeKind
from agent_bridge_worker.policy import ScopedPolicy
from agent_bridge_worker.tests.test_bridge_worker import LEASE, TASK_ID
from agent_comms.tests.fakes import FakeClock

CAPS  = { "browser": { "inspect": False, "implement": True }}
MODES = frozenset({ "read_only", "workspace_write" })


def task( prompt: dict | str, operation: str = "implement", **over ) -> ClaimedTask:
    mode   = "read_only" if operation == "inspect" else "workspace_write"
    fields = dict( task_id=TASK_ID, lease_token=LEASE, lease_expires_at=0, lease_seconds=120, attempt=1, agent="browser",
                   workspace="browser", operation=operation, approval_mode=mode,
                   prompt=prompt if isinstance( prompt, str ) else json.dumps( prompt ))
    fields.update( over )
    return ClaimedTask( **fields )


class FakeRunning:
    def __init__( self, answer, ticks=0, error=None ):
        self.answer, self.ticks, self.error, self.stopped = answer, ticks, error, False

    def poll( self ):
        if self.ticks > 0:
            self.ticks -= 1
            return None
        if self.error:
            raise BrowserCommandFailed( self.error )
        return self.answer

    def stop( self ):
        self.stopped = True


class FakeCommands:
    def __init__( self, answer=None, **kw ):
        self.answer, self.kw, self.calls, self.last = answer, kw, [], None

    def start( self, operation, flags, confirm ):
        self.calls.append(( operation, flags, confirm ))
        self.last = FakeRunning( self.answer, **self.kw )
        return self.last


def verified( line="chrome started on this computer (Chrome/1); 1 tab(s) open.", **data ) -> dict:
    return { "ok": True, "line": line, "adw_id": "abcd1234", "data": { "verified": True, **data }}


def run( commands, t, signals=None ):
    seq = iter( signals or [] )
    return BrowserAgent( commands, FakeClock(), poll_seconds=0.1 ).run( t, Path( "." ), lambda: next( seq, "continue" ))


def test_open_runs_the_adw_and_reports_the_verified_result_with_its_execution_id():
    commands = FakeCommands( verified( started=True ))
    out = run( commands, task({ "browser": "open", "url": "https://example.com", "target": "chrome" }))
    assert commands.calls == [( "open", { "url": "https://example.com", "browser": "chrome" }, False )]
    assert out.kind == OutcomeKind.SUCCEEDED and out.details[ "adw_id" ] == "abcd1234" and out.details[ "verified" ] is True


def test_a_queued_or_unverified_answer_is_not_a_success():
    out = run( FakeCommands({ "ok": True, "line": "did something", "data": {}}), task({ "browser": "refresh" }))
    assert out.kind == OutcomeKind.FAILED and out.retryable is False and out.details[ "verified" ] is False


def test_a_refusal_from_the_adw_is_a_failure_with_its_words():
    out = run( FakeCommands({ "ok": False, "line": "I did not click: 'Pay' looks consequential.", "data": { "needs_confirmation": True }}),
               task({ "browser": "click", "text": "Pay now" }))
    assert out.kind == OutcomeKind.FAILED and "consequential" in out.summary and out.details[ "needs_confirmation" ]


def test_confirm_is_passed_only_when_it_is_exactly_true():
    commands = FakeCommands( verified())
    run( commands, task({ "browser": "click", "text": "Pay", "confirm": True }))
    run( commands, task({ "browser": "click", "text": "Pay", "confirm": "yes" }))
    assert [ c[ 2 ] for c in commands.calls ] == [ True, False ]


def test_reads_run_as_inspect_and_return_the_page_text_in_the_body():
    commands = FakeCommands( verified( "Example at https://e.example: 5 characters.", text="Hello" ))
    out = run( commands, task({ "browser": "read" }, "inspect" ))
    assert out.kind == OutcomeKind.SUCCEEDED and out.body.endswith( "Hello" )


def test_the_operation_and_the_mode_must_agree():
    assert run( FakeCommands(), task({ "browser": "read" }, "implement" )).kind == OutcomeKind.REFUSED
    assert run( FakeCommands(), task({ "browser": "open" }, "inspect" )).kind == OutcomeKind.REFUSED


def test_free_text_unknown_fields_and_missing_values_are_refused_before_anything_runs():
    commands = FakeCommands( verified())
    for prompt in ( "open a browser", { "browser": "format_disk" }, { "browser": "open", "cmd": "calc.exe" },
                    { "browser": "navigate" }, { "browser": "close" }, { "browser": "select_tab" }, { "browser": "click" },
                    { "browser": "open", "url": "x" * 600 }, { "browser": "open", "url": [ "a" ] }):
        assert run( commands, task( prompt )).kind == OutcomeKind.REFUSED, prompt
    assert commands.calls == []


def test_only_chrome_can_be_targeted():
    commands = FakeCommands( verified())
    for other in ( "edge", "msedge", "firefox" ):
        assert run( commands, task({ "browser": "open", "target": other })).kind == OutcomeKind.REFUSED
    assert run( commands, task({ "browser": "open", "target": "chrome" })).kind == OutcomeKind.SUCCEEDED
    assert len( commands.calls ) == 1


def test_the_public_spelling_browser_dot_open_is_accepted():
    commands = FakeCommands( verified())
    assert run( commands, task({ "browser": "browser.open" })).kind == OutcomeKind.SUCCEEDED and commands.calls[ 0 ][ 0 ] == "open"


def test_a_crashed_adw_is_an_honest_failure_not_a_retry():
    out = run( FakeCommands( error="exit status 1, no outcome file" ), task({ "browser": "open" }))
    assert out.kind == OutcomeKind.FAILED and out.retryable is False and "no outcome" in out.summary


def test_a_read_is_stopped_when_cancelled_but_a_change_runs_to_its_end():
    reading = FakeCommands( verified(), ticks=3 )
    assert run( reading, task({ "browser": "tabs" }, "inspect" ), signals=[ "cancel" ]).kind == OutcomeKind.CANCELLED or reading.last.stopped
    changing = FakeCommands( verified(), ticks=3 )
    out = run( changing, task({ "browser": "navigate", "url": "https://a.example" }), signals=[ "continue", "cancel" ])
    assert out.kind == OutcomeKind.SUCCEEDED and not changing.last.stopped


def test_the_policy_lets_the_browser_agent_do_both_modes_for_gateway_senders_only():
    policy = ScopedPolicy( CAPS | { "browser": { "inspect": False, "implement": True }}, frozenset({ "browser" }), MODES )
    assert policy.refusal( task({ "browser": "open" })) is None
    assert policy.refusal( task({ "browser": "read" }, "inspect" )) is None
    assert policy.refusal( task({ "browser": "open" }, sender="someone-else" )) is not None
    assert policy.refusal( task({ "browser": "open" }, workspace="browser", agent="notes" )) is not None


def test_the_uv_adapter_builds_a_fixed_argv_and_values_cannot_become_options():
    commands = UvBrowserCommands( Path( "/uv" ), Path( "/adw.py" ), Path( "." ))
    argv = commands.argv( "click", { "text": "--help", "browser": "chrome" }, True, "/tmp/r.json" )
    assert argv == [ "/uv", "run", "--quiet", "/adw.py", "browser.click", "--browser=chrome", "--text=--help", "--confirm", "--json-out=/tmp/r.json" ]
    try:
        commands.argv( "rm", {}, False, "/tmp/r" )
    except BrowserCommandFailed:
        return
    raise AssertionError( "an unknown operation must not become a command" )


def test_form_controls_is_an_inspect_task_and_carries_the_selectors_back():
    """The fix for guessed CSS selectors: the agent is told what is on the page before it operates it."""
    controls = [{ "role": "dropdown", "css": "#ddl", "label": "Record Type:",
                  "options": [{ "position": 2, "value": "OLSR/PLAT/NA/NA", "text": "PLAT Records" }]}]
    commands = FakeCommands( verified( "that page has 25 control(s).", total=25, controls=controls ))
    out = run( commands, task({ "browser": "form_controls", "limit": 80 }, operation="inspect" ))
    assert commands.calls == [( "form_controls", { "limit": "80" }, False )]
    assert out.kind == OutcomeKind.SUCCEEDED and out.details[ "controls" ][ 0 ][ "css" ] == "#ddl"
    assert run( commands, task({ "browser": "form_controls" }, operation="implement" )).kind == OutcomeKind.REFUSED


def test_select_option_is_an_implement_task_named_by_css_or_label():
    commands = FakeCommands( verified( "Chose 'PLAT Records' (value 'OLSR/PLAT/NA/NA') in that dropdown; the page confirms it.",
                                       selected="PLAT Records", selected_value="OLSR/PLAT/NA/NA" ))
    out = run( commands, task({ "browser": "select_option", "css": "#ddl", "option_text": "PLAT Records" }))
    assert commands.calls == [( "select_option", { "css": "#ddl", "option-text": "PLAT Records" }, False )]
    assert out.kind == OutcomeKind.SUCCEEDED and out.details[ "selected" ] == "PLAT Records"
    assert run( commands, task({ "browser": "select_option", "value": "x" })).kind == OutcomeKind.REFUSED
    assert run( commands, task({ "browser": "select_option", "css": "#ddl" })).kind == OutcomeKind.REFUSED


def test_a_dropdown_item_is_never_asked_for_with_text():
    """`text` is what to type, not what to choose: a request that confuses them is refused here rather
    than finding the wrong control."""
    refused = run( FakeCommands( verified()), task({ "browser": "select_option", "text": "PLAT Records" }))
    assert refused.kind == OutcomeKind.REFUSED and "not a locator" in refused.summary


def test_fill_needs_both_a_control_and_the_words_to_type():
    commands = FakeCommands( verified( "Typed \'Fralick\'s Addition\' into \'Project Name:\'; the field reads it back.",
                                       value="Fralick's Addition" ))
    out = run( commands, task({ "browser": "fill", "css": "#txt", "text": "Fralick's Addition" }))
    assert commands.calls == [( "fill", { "css": "#txt", "text": "Fralick's Addition" }, False )]
    assert out.kind == OutcomeKind.SUCCEEDED
    assert run( commands, task({ "browser": "fill", "css": "#txt" })).kind == OutcomeKind.REFUSED
    assert run( commands, task({ "browser": "fill", "text": "x" })).kind == OutcomeKind.REFUSED


def test_an_empty_fill_is_how_a_field_is_emptied():
    """Everywhere else an empty value means nothing was given; for fill it is a real instruction."""
    commands = FakeCommands( verified( "Typed '' into 'Project Name:'; the field reads it back.", value="" ))
    out = run( commands, task({ "browser": "fill", "css": "#txt", "text": "" }))
    assert commands.calls == [( "fill", { "css": "#txt", "text": "" }, False )] and out.kind == OutcomeKind.SUCCEEDED


def test_a_refused_credential_field_is_a_failure_and_the_words_never_come_back():
    answer = { "ok": False, "adw_id": "beef0001",
               "line": "I did not touch that field: that is a password field, and I never touch those.",
               "data": { "verified": False, "cause": "credential_field" }}
    out = run( FakeCommands( answer ), task({ "browser": "fill", "css": "#pw", "text": "hunter2" }))
    assert out.kind == OutcomeKind.FAILED and not out.retryable and out.details[ "cause" ] == "credential_field"
    assert "hunter2" not in out.summary and "hunter2" not in out.body


def test_an_unverified_form_result_is_never_reported_as_done():
    """"the form was submitted" is not "it worked": only the ADW's own verification counts."""
    answer = { "ok": True, "adw_id": "beef0002", "line": "the list still holds ''.", "data": { "verified": False }}
    assert run( FakeCommands( answer ), task({ "browser": "select_option", "css": "#ddl", "value": "x" })).kind == OutcomeKind.FAILED


def test_a_form_operation_names_its_tab_by_the_stable_id():
    commands = FakeCommands( verified( "Chose it.", selected="PLAT Records" ))
    run( commands, task({ "browser": "select_option", "css": "#ddl", "value": "v", "tab": "D4E512DAA70B7741" }))
    assert commands.calls[ 0 ][ 1 ][ "tab" ] == "D4E512DAA70B7741"


def test_download_is_a_change_that_needs_a_locator_and_carries_the_file_back():
    """A click cannot keep a file, so download is its own operation -- and its own lane."""
    refused = run( FakeCommands( verified()), task({ "browser": "download" }))
    assert refused.kind == OutcomeKind.REFUSED and "needs text, label or css" in refused.summary
    wrong_lane = run( FakeCommands( verified()), task({ "browser": "download", "text": "0337201.tif" },
                                                      operation="inspect" ))
    assert wrong_lane.kind == OutcomeKind.REFUSED and "implement" in wrong_lane.summary
    commands = FakeCommands( verified( "Saved 0337201.tif (84.2 KB) to C:\\Users\\eg\\Downloads\\BrowserRecorder.",
                                       files=[ { "name": "0337201.tif", "bytes": 86234 } ],
                                       download_directory=r"C:\Users\eg\Downloads\BrowserRecorder" ))
    out = run( commands, task({ "browser": "download", "text": "0337201.tif" }))
    assert commands.calls == [( "download", { "text": "0337201.tif" }, False )]
    assert out.kind == OutcomeKind.SUCCEEDED
    assert out.details[ "files" ][ 0 ][ "name" ] == "0337201.tif"


def test_a_download_that_saved_nothing_is_not_reported_as_done():
    commands = FakeCommands( { "ok": False, "line": "I pressed '0337201.tif', but no file arrived in "
                                                     "C:\\Users\\eg\\Downloads\\BrowserRecorder within 60s.",
                               "data": { "verified": False } })
    out = run( commands, task({ "browser": "download", "text": "0337201.tif" }))
    assert out.kind != OutcomeKind.SUCCEEDED and "no file arrived" in ( out.body or out.summary )



class FakeUploader:
    def __init__( self, fail=None ):
        self.blobs, self.manifests, self.fail = [], [], fail

    def upload_download_blob( self, task_id, sha256, data ):
        if self.fail:
            from agent_bridge_worker.ports import GatewayError
            raise GatewayError( 409, "lease_invalid", self.fail )
        self.blobs.append(( task_id, sha256, data ))

    def post_downloads( self, task_id, manifest ):
        self.manifests.append( manifest )
        return { "files": [{ "idx": f[ "idx" ], "file_id": f"f_{ f[ 'idx' ]:024x}" } for f in manifest[ "files" ]]}


def a_downloaded_file( tmp: Path ) -> dict:
    import hashlib
    original, page = tmp / "0337201.tif", tmp / "page-001-full.png"
    original.write_bytes( b"II*\x00a-plat" )
    page.write_bytes( b"\x89PNG-page-one" )
    digest = lambda p: hashlib.sha256( p.read_bytes()).hexdigest()
    return { "name": "0337201.tif", "original_name": "0337201.tif", "path": str( original ), "chrome_path": "C:\\x\\0337201.tif",
             "bytes": original.stat().st_size, "sha256": digest( original ), "mime": "image/tiff", "status": "saved",
             "inspection": { "kind": "image", "mime": "image/tiff", "page_count": 1, "text": "", "note": "",
                             "pages": [{ "page": 1, "rendition": "full", "path": str( page ), "mime": "image/png",
                                         "width": 10, "height": 10, "bytes": page.stat().st_size, "sha256": digest( page )}]}}


def run_publishing( uploader, files ):
    from agent_bridge_worker.download_publisher import DownloadPublisher
    commands = FakeCommands( verified( "Saved 0337201.tif (0.0 KB).", files=files ))
    agent = BrowserAgent( commands, FakeClock(), poll_seconds=0.1, publisher=DownloadPublisher( uploader ))
    return agent.run( task({ "browser": "download", "text": "0337201.tif" }), Path( "." ), lambda: "continue" )


def test_a_saved_download_is_published_and_given_a_file_id():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        uploader = FakeUploader()
        out = run_publishing( uploader, [ a_downloaded_file( Path( tmp ))])
    assert out.kind == OutcomeKind.SUCCEEDED and out.details[ "inspectable" ] is True
    assert len( uploader.blobs ) == 2 and uploader.manifests[ 0 ][ "lease_token" ] == LEASE
    sent = uploader.manifests[ 0 ][ "files" ][ 0 ]
    assert sent[ "original_stored" ] is True and sent[ "pages" ][ 0 ][ "rendition" ] == "full" and "path" not in sent[ "pages" ][ 0 ]
    kept = out.details[ "files" ][ 0 ]
    assert kept[ "file_id" ] == "f_" + "0" * 24 and "pages" not in kept[ "inspection" ]
    assert "browser_view_file" in out.body


def test_a_publish_that_fails_keeps_the_download_but_says_it_cannot_be_viewed():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        out = run_publishing( FakeUploader( fail="lease gone" ), [ a_downloaded_file( Path( tmp ))])
    assert out.kind == OutcomeKind.SUCCEEDED and out.details[ "inspectable" ] is False
    assert "could not be published for viewing" in out.body


class FlakyUploader( FakeUploader ):
    """The live box's network drops for seconds at a time (`[Errno 101] Network is unreachable`, 2026-11-09)."""
    def __init__( self, outages, status=0 ):
        super().__init__()
        self.outages, self.status, self.attempts = outages, status, 0

    def upload_download_blob( self, task_id, sha256, data ):
        self.attempts += 1
        if self.outages > 0:
            self.outages -= 1
            from agent_bridge_worker.ports import GatewayError
            raise GatewayError( self.status, "unreachable", "[Errno 101] Network is unreachable" )
        super().upload_download_blob( task_id, sha256, data )


def publish_with( uploader, files ):
    from agent_bridge_worker.download_publisher import DownloadPublisher
    waits = []
    publisher = DownloadPublisher( uploader, sleep=waits.append )
    commands = FakeCommands( verified( "Saved 0337201.tif (0.0 KB).", files=files ))
    agent = BrowserAgent( commands, FakeClock(), poll_seconds=0.1, publisher=publisher )
    return agent.run( task({ "browser": "download", "text": "0337201.tif" }), Path( "." ), lambda: "continue" ), waits


def test_a_network_blip_while_publishing_is_retried_and_the_file_becomes_viewable():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        uploader = FlakyUploader( outages=2 )
        out, waits = publish_with( uploader, [ a_downloaded_file( Path( tmp ))])
    assert out.details[ "inspectable" ] is True and out.details[ "files" ][ 0 ][ "file_id" ]
    assert len( waits ) == 2 and len( uploader.manifests ) == 1


def test_a_refusal_from_the_gateway_is_not_retried():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        uploader = FlakyUploader( outages=5, status=409 )
        out, waits = publish_with( uploader, [ a_downloaded_file( Path( tmp ))])
    assert out.details[ "inspectable" ] is False and waits == [] and uploader.attempts == 1


def test_an_outage_that_outlasts_the_retries_is_reported_with_how_often_it_was_tried():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        uploader = FlakyUploader( outages=99 )
        out, waits = publish_with( uploader, [ a_downloaded_file( Path( tmp ))])
    assert out.details[ "inspectable" ] is False and "4 attempts" in out.details[ "inspect_problem" ]
    assert sum( waits ) <= 30


def test_a_file_that_changed_after_hashing_is_not_published_under_the_old_checksum():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        f = a_downloaded_file( Path( tmp ))
        Path( f[ "path" ]).write_bytes( b"something else" )
        uploader = FakeUploader()
        out = run_publishing( uploader, [ f ])
    assert out.details[ "inspectable" ] is False and "changed after it was checked" in out.details[ "inspect_problem" ]
    assert uploader.blobs == [] and uploader.manifests == []


def a_text_file( tmp: Path, name: str, text: str ) -> dict:
    import hashlib
    path = tmp / name
    path.write_text( text, encoding="utf-8" )
    return { "name": name, "original_name": f"docs.zip > { name }", "path": str( path ), "chrome_path": "",
             "bytes": path.stat().st_size, "sha256": hashlib.sha256( path.read_bytes()).hexdigest(), "mime": "text/plain",
             "status": "unpacked", "from_archive": "docs.zip",
             "inspection": { "kind": "text", "mime": "text/plain", "page_count": 0, "text": text, "note": "", "pages": []}}


def test_an_archive_with_more_members_than_the_gateway_takes_publishes_twenty_and_names_the_rest():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        files = [ a_text_file( Path( tmp ), f"m{ i:02d}.txt", f"member { i }" ) for i in range( 24 )]
        uploader = FakeUploader()
        out = run_publishing( uploader, files )
    assert len( uploader.manifests[ 0 ][ "files" ]) == 20
    assert out.details[ "inspectable" ] is True and len( out.details[ "files" ]) == 24
    held = [ f for f in out.details[ "files" ] if f.get( "published" ) is False ]
    assert [ f[ "name" ] for f in held ] == [ "m20.txt", "m21.txt", "m22.txt", "m23.txt" ]
    assert "m23.txt" not in out.body.split( "View with browser_view_file:" )[ 1 ].split( "\n" )[ 0 ]
    assert "4 more file(s) are on the desktop only" in out.body


def test_all_the_text_together_fits_the_gateways_budget_and_short_files_keep_theirs():
    import tempfile
    from agent_bridge_worker.download_publisher import TEXT_BUDGET
    with tempfile.TemporaryDirectory() as tmp:
        files = [ a_text_file( Path( tmp ), "short.txt", "Liber 4 of Plats, page 31" )]
        files += [ a_text_file( Path( tmp ), f"long{ i }.txt", "é" * 90_000 ) for i in range( 3 )]   # 180 KB each in UTF-8
        uploader = FakeUploader()
        run_publishing( uploader, files )
    sent = uploader.manifests[ 0 ][ "files" ]
    assert sum( len( f[ "text" ].encode( "utf-8" )) for f in sent ) <= TEXT_BUDGET
    assert sent[ 0 ][ "text" ] == "Liber 4 of Plats, page 31" and sent[ 0 ][ "text_truncated" ] is False
    assert all( f[ "text_truncated" ] for f in sent[ 1: ])
    assert all( f[ "text" ].encode( "utf-8" ).decode( "utf-8" ) for f in sent[ 1: ])


TESTS = [ v for k, v in dict( globals()).items() if k.startswith( "test_" )]
if __name__ == "__main__":
    for test in TESTS:
        test()
        print( "ok", test.__name__ )
    print( f"{ len( TESTS )} passed" )


def test_dashboard_notes_is_one_implement_task_with_no_fields():
    commands = FakeCommands( verified( "Chrome is open on the dashboard with the NOTES tab showing.", notes_active=True ))
    out = run( commands, task({ "browser": "dashboard_notes" }, operation="implement" ))
    assert commands.calls == [( "dashboard_notes", {}, False )]
    assert out.kind == OutcomeKind.SUCCEEDED and out.details[ "notes_active" ] is True
    refused = run( FakeCommands( verified()), task({ "browser": "dashboard_notes" }, operation="inspect" ))
    assert refused.kind == OutcomeKind.REFUSED and "runs as implement" in refused.summary
