"""Scoped write access, offline: real git and a real post office in tmpdirs; only the gateway and Codex are doubles.

    cd lancedb_memory
    .venv/bin/python -m agent_bridge_worker.tests.test_write_access
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from agent_bridge_worker.adapters.git_changes import GitChangeTracker
from agent_bridge_worker.adapters.http_gateway import HttpTaskGateway, clip
from agent_bridge_worker.adapters.jailed_checks import JailedCheckRunner
from agent_bridge_worker.adapters.json_journal import JsonWriteJournal
from agent_bridge_worker.adapters.post_office_agent import PostOfficeAgent
from agent_bridge_worker.adapters.workspace_lock import FlockWorkspaceLock
from agent_bridge_worker.models import ClaimedTask, OutcomeKind, WorkspaceConfig
from agent_bridge_worker.policy import ScopedPolicy, scoped_operations
from agent_bridge_worker.ports import WriteBlocked
from agent_bridge_worker.redaction import SecretScanner
from agent_bridge_worker.service import BridgeTaskRun
from agent_bridge_worker.tests.test_bridge_worker import LEASE, TASK_ID, FakeGateway
from agent_bridge_worker.workspaces import ConfiguredWorkspaces
from agent_bridge_worker.write_session import ScopedWriteSession
from agent_comms.adapters.launchers import RecordingLauncher
from agent_comms.executors.basic import ScriptedExecutor
from agent_comms.factory import build_post_office
from agent_comms.models import TaskRequest, TaskResult
from agent_comms.tests.fakes import FakeClock

GIT_ENV = { "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t" }
CHECK   = [ sys.executable, "-c", "import pathlib,sys; sys.exit(0 if pathlib.Path('NOTES.md').exists() else 3)" ]


def test_toyota_and_chatgpt_identities_get_the_same_worker_policy() -> None:
    """EG's decision (2026-11-06): the gateway identity differs, the permissions do not."""
    policy = ScopedPolicy({"codex-writer": {"implement": True}}, frozenset({"agent_blocks_dev"}))
    def task( sender: str ) -> ClaimedTask:
        return ClaimedTask(task_id=TASK_ID, lease_token=LEASE, lease_expires_at=0, lease_seconds=60,
                           attempt=1, agent="codex-writer", workspace="agent_blocks_dev", operation="implement",
                           approval_mode="workspace_write", prompt="x", sender=sender)
    assert policy.refusal( task( "toyota-bridge" )) is None
    assert policy.refusal( task( "chatgpt-bridge" )) is None
    assert policy.refusal( task( "someone-else" )) == "gateway sender is not approved"


def git( root: Path, *args: str ) -> str:
    return subprocess.run([ "git", "-C", str( root ), *args ], capture_output=True, text=True, check=True,
                          env={ **os.environ, **GIT_ENV }).stdout


def sandbox( base: Path, name: str = "bridge_sandbox" ) -> Path:
    root = base / "bridge_workspaces" / name
    root.mkdir( parents=True )
    git( root, "init", "-q" )
    ( root / "README.md" ).write_text( "sandbox\n" )
    ( root / ".gitignore" ).write_text( "ignored/\n" )
    git( root, "add", "-A" )
    git( root, "commit", "-qm", "start" )
    return root


class WritingExecutor:
    """Codex stand-in: writes what it is told into the workdir it is handed, then answers."""

    def __init__( self, files: dict[ str, str ], ok: bool = True ) -> None:
        self.files, self.ok, self.seen = files, ok, []

    @property
    def performer( self ) -> str:
        return "codex:fake-writer"

    async def execute( self, envelope ):
        self.seen.append( envelope )
        root = Path( envelope.context[ "workdir" ])
        for rel, text in self.files.items():
            ( root / rel ).parent.mkdir( parents=True, exist_ok=True )
            ( root / rel ).write_text( text )
        if self.ok:
            return TaskResult( ok=True, performer=self.performer, text="Created the file.\nDone." )
        return TaskResult( ok=False, performer=self.performer, error="codex stopped half way" )


class PassingChecks:
    def __init__( self ) -> None:
        self.ran = []

    def run( self, workdir, command ):
        self.ran.append( command )
        return JailedCheckRunner( lambda argv, root: argv ).run( workdir, command )


class WriteHarness:
    def __init__( self, gateway: FakeGateway, writer: WritingExecutor, base: Path | None = None,
                  checks: list | None = None ) -> None:
        self.base    = base or Path( tempfile.mkdtemp( prefix="bridge_write_" ))
        self.root    = self.base / "bridge_workspaces" / "bridge_sandbox"
        if not self.root.exists():
            sandbox( self.base )
        self.clock   = FakeClock()
        self.gateway = gateway
        self.writer  = writer
        self.kit = build_post_office( root=self.base / "po", launcher=RecordingLauncher(), clock=self.clock,
                                      builders={ "codex_cli": lambda card: ScriptedExecutor([ TaskResult(
                                                     ok=True, performer="codex:fake", text="read answer" )]),
                                                 "codex_cli_write": lambda card: writer },
                                      forward_failures_to_mechanic=False )
        self.clock.on_sleep = lambda: asyncio.run( self._drain())
        agent = lambda: PostOfficeAgent( "chatgpt-bridge", self.kit.dispatcher, self.kit.replies, self.kit.canceller,
                                         self.kit.board, self.kit.event_log, TaskRequest, self.clock, timeout_seconds=120 )
        offered = scoped_operations( self.kit.registry, "chatgpt-bridge" )
        self.ws = ConfiguredWorkspaces( WorkspaceConfig.model_validate({
            "write_root": str( self.base / "bridge_workspaces" ),
            "workspaces": { "agent_blocks": { "path": str( self.base / "po" ), "writes": False },
                            "bridge_sandbox": { "path": str( self.root ), "writes": True,
                                                "checks": checks if checks is not None else [ CHECK ]}}}), protected=[])
        self.checks  = PassingChecks()
        self.journal = JsonWriteJournal( self.base / "journal" )
        self.session = ScopedWriteSession( FlockWorkspaceLock( self.base / "journal" / "locks" ), GitChangeTracker(),
                                           self.checks, self.journal, self.ws.checks, self._settled,
                                           SecretScanner( known=[ "W" * 40 ]), write_problem=self.ws.write_problem )
        agents = { name: agent() for name in offered }
        self.run = BridgeTaskRun( gateway, ScopedPolicy( offered, self.ws.write_names()), self.ws, agents,
                                  agents[ "codex" ], self.clock, "worker-test", writes=self.session )

    async def _drain( self ) -> None:
        for name in ( "codex", "codex-writer" ):
            await self.kit.worker_for( name ).drain()

    def _settled( self, local: str ) -> bool:
        e = self.kit.board.task( local )
        return e is None or e.status.value in ( "succeeded", "failed", "cancelled", "timed_out" )

    def close( self ) -> None:
        shutil.rmtree( self.base, ignore_errors=True )


def write_gateway( **kw ) -> FakeGateway:
    g = FakeGateway( operation="implement", agent="codex-writer", mode="workspace_write", workspace="bridge_sandbox", **kw )
    g._claim[ "prompt" ] = "Create NOTES.md with a heading Hello."
    return g


def task( **over ) -> ClaimedTask:
    data = dict( task_id=TASK_ID, lease_token=LEASE, lease_expires_at=0, lease_seconds=60, attempt=1, agent="codex-writer",
                 workspace="bridge_sandbox", operation="implement", approval_mode="workspace_write", prompt="x" )
    data.update( over )
    return ClaimedTask.model_validate( data )


# -- policy ---------------------------------------------------------------------

def test_policy_matrix() -> None:
    policy = ScopedPolicy({ "codex": { "explain": False, "inspect": False }, "codex-writer": { "implement": True }},
                          frozenset({ "bridge_sandbox" }))
    assert policy.refusal( task()) is None
    assert policy.refusal( task( agent="codex", workspace="agent_blocks", operation="explain", approval_mode="read_only" )) is None
    assert "only in workspace_write" in policy.refusal( task( approval_mode="read_only" ))
    assert "only in read_only" in policy.refusal( task( agent="codex", operation="explain" ))
    assert "does not offer implement" in policy.refusal( task( agent="codex" ))
    assert "not write-enabled" in policy.refusal( task( workspace="agent_blocks" ))
    assert "not one this worker runs" in policy.refusal( task( operation="delete_all" ))
    assert "does not run tasks" in policy.refusal( task( agent="hello-world" ))
    read_only = ScopedPolicy({ "codex-writer": { "implement": True }}, frozenset({ "bridge_sandbox" }),
                             modes=frozenset({ "read_only" }))
    assert "not enabled" in read_only.refusal( task())


def test_registry_offers_writes_only_where_the_card_declares_them() -> None:
    kit = build_post_office( root=Path( tempfile.mkdtemp()), launcher=RecordingLauncher(), clock=FakeClock(),
                             forward_failures_to_mechanic=False )
    offered = scoped_operations( kit.registry, "chatgpt-bridge" )
    assert offered[ "codex-writer" ] == { "implement": True }
    assert offered[ "claude-sonnet" ] == { "inspect": False, "explain": False, "search_memory": False, "implement": True }
    writers = { "codex-writer", "claude-sonnet" }
    assert all( not writes for name, ops in offered.items() if name not in writers for writes in ops.values())
    assert all( not writes for name in writers for op, writes in offered[ name ].items() if op != "implement" )


# -- workspaces -------------------------------------------------------------------

def test_workspace_resolution_refuses_escapes() -> None:
    base = Path( tempfile.mkdtemp())
    try:
        good  = sandbox( base )
        plain = base / "bridge_workspaces" / "not_git"
        plain.mkdir()
        outside = base / "elsewhere"
        outside.mkdir()
        git( outside, "init", "-q" )
        link = base / "bridge_workspaces" / "linked"
        link.symlink_to( outside )
        nested = good / "inner"
        nested.mkdir()
        cfg = WorkspaceConfig.model_validate({ "write_root": str( base / "bridge_workspaces" ), "workspaces": {
            "good": { "path": str( good ), "writes": True }, "plain": { "path": str( plain ), "writes": True },
            "outside": { "path": str( outside ), "writes": True }, "linked": { "path": str( link ), "writes": True },
            "nested": { "path": str( nested ), "writes": True }, "relative": { "path": "bridge_sandbox", "writes": True },
            "ro": { "path": str( good ), "writes": False }}})
        ws = ConfiguredWorkspaces( cfg, protected=[])
        assert ws.write_problem( "good" ) is None and ws.write_names() == frozenset({ "good" })
        assert "not a git repository" in ws.write_problem( "plain" )
        assert "not directly under" in ws.write_problem( "outside" )
        assert "symlink" in ws.write_problem( "linked" )
        assert "not directly under" in ws.write_problem( "nested" )
        assert "absolute" in ws.write_problem( "relative" )
        assert "read-only" in ws.write_problem( "ro" )
        assert "no workspace" in ws.write_problem( "nope" )
        guarded = ConfiguredWorkspaces( cfg, protected=[ good / "agent_bridge_worker" ])
        ( good / "agent_bridge_worker" ).mkdir()
        assert "controls the bridge" in guarded.write_problem( "good" )
    finally:
        shutil.rmtree( base )


def test_an_extra_write_path_is_the_only_exception_to_the_write_root() -> None:
    base = Path( tempfile.mkdtemp())
    try:
        sandbox( base )
        named, other = base / "project", base / "other_project"
        for repo in ( named, other ):
            repo.mkdir()
            git( repo, "init", "-q" )
        cfg = WorkspaceConfig.model_validate({ "write_root": str( base / "bridge_workspaces" ),
            "extra_write_paths": [ str( named )], "workspaces": {
            "named": { "path": str( named ), "writes": True }, "other": { "path": str( other ), "writes": True },
            "named_ro": { "path": str( named ), "writes": False }}})
        ws = ConfiguredWorkspaces( cfg, protected=[])
        assert ws.write_problem( "named" ) is None
        assert "not directly under" in ws.write_problem( "other" )
        assert "read-only" in ws.write_problem( "named_ro" )
        guarded = ConfiguredWorkspaces( cfg, protected=[ named / "agent_bridge_worker" ])
        ( named / "agent_bridge_worker" ).mkdir()
        assert "controls the bridge" in guarded.write_problem( "named" )
    finally:
        shutil.rmtree( base )


# -- change tracking ------------------------------------------------------------------

def test_tracker_ignores_existing_user_changes_and_leaves_the_index_alone() -> None:
    base = Path( tempfile.mkdtemp())
    try:
        root = sandbox( base )
        ( root / "README.md" ).write_text( "the user's own edit\n" )
        git( root, "add", "README.md" )                          # staged by the user
        ( root / "draft.txt" ).write_text( "untracked user file\n" )
        index_before = git( root, "diff", "--cached", "--name-only" )
        tracker  = GitChangeTracker()
        baseline = tracker.snapshot( root )
        ( root / "NOTES.md" ).write_text( "# Hello\n" )
        ( root / "ignored" ).mkdir()
        ( root / "ignored" / "x.txt" ).write_text( "ignored\n" )
        changes = tracker.changes( root, baseline )
        assert [( p.path, p.status ) for p in changes.paths ] == [( "NOTES.md", "added" )], changes.paths
        assert "+# Hello" in changes.diff and "the user's own edit" not in changes.diff
        assert git( root, "diff", "--cached", "--name-only" ) == index_before
        assert ( root / "README.md" ).read_text() == "the user's own edit\n"
        assert ( root / "draft.txt" ).exists()
    finally:
        shutil.rmtree( base )


# -- whole runs -------------------------------------------------------------------------

def test_markdown_write_returns_exact_contents_and_runs_checks() -> None:
    text = "# Hello\n\nWritten through the bridge.\n"
    h = WriteHarness( write_gateway(), WritingExecutor({ "NOTES.md": text }))
    try:
        report = h.run.run_one()
        assert report.outcome is OutcomeKind.SUCCEEDED, report
        done = h.gateway.completed
        assert done[ "ok" ] is True
        d = done[ "details" ]
        assert d[ "outcome" ] == "changed" and d[ "effective_mode" ] == "workspace_write"
        assert [ p[ "path" ] for p in d[ "changed_paths" ]] == [ "NOTES.md" ]
        art = d[ "artifacts" ][ 0 ]
        assert art[ "content" ] == text and art[ "complete" ] and art[ "size" ] == len( text.encode())
        assert "not a file download" in d[ "delivery" ] or "Nothing here is a file download" in d[ "delivery" ]
        assert d[ "checks_run" ][ 0 ][ "passed" ] is True and h.checks.ran == [ CHECK ]
        assert d[ "diff" ][ "diff_complete" ] and "+# Hello" in d[ "diff" ][ "diff" ]
        assert Path( d[ "diff" ][ "local_patch" ]).is_file()
        assert ( h.root / "NOTES.md" ).read_text() == text
        assert h.writer.seen[ 0 ].context[ "workdir" ] == str( h.root )
        json.dumps( d )                                            # it travels as JSON
    finally:
        h.close()


def test_read_only_request_for_a_file_changes_nothing() -> None:
    g = FakeGateway( operation="explain", workspace="bridge_sandbox" )
    g._claim[ "prompt" ] = "Please create NOTES.md in the sandbox."
    writer = WritingExecutor({ "NOTES.md": "x" })
    h = WriteHarness( g, writer )
    try:
        report = h.run.run_one()
        assert report.outcome is OutcomeKind.SUCCEEDED
        assert h.gateway.completed[ "details" ] is None and writer.seen == []
        assert not ( h.root / "NOTES.md" ).exists()
        assert git( h.root, "status", "--porcelain" ) == ""
    finally:
        h.close()


def test_write_mode_cannot_be_smuggled_into_a_read_operation() -> None:
    for over in ({ "operation": "explain", "mode": "workspace_write" }, { "operation": "implement", "mode": "read_only" }):
        g = FakeGateway( agent="codex-writer", workspace="bridge_sandbox", **over )
        writer = WritingExecutor({ "NOTES.md": "x" })
        h = WriteHarness( g, writer )
        try:
            report = h.run.run_one()
            assert report.outcome is OutcomeKind.REFUSED, report
            assert writer.seen == [] and not ( h.root / "NOTES.md" ).exists()
        finally:
            h.close()


def test_unknown_or_read_only_workspace_is_refused_for_writes() -> None:
    for name in ( "nowhere", "agent_blocks" ):
        g = write_gateway()
        g._claim[ "workspace" ] = name
        writer = WritingExecutor({ "NOTES.md": "x" })
        h = WriteHarness( g, writer )
        try:
            assert h.run.run_one().outcome is OutcomeKind.REFUSED
            assert writer.seen == []
        finally:
            h.close()


def test_failure_after_edits_reports_the_changed_paths() -> None:
    h = WriteHarness( write_gateway(), WritingExecutor({ "src/half.py": "x = 1\n" }, ok=False ))
    try:
        report = h.run.run_one()
        assert report.outcome is OutcomeKind.FAILED
        done = h.gateway.completed
        assert done[ "ok" ] is False and "changed 1 file" in done[ "summary" ]
        d = done[ "details" ]
        assert d[ "outcome" ] == "partial" and d[ "changed_paths" ][ 0 ][ "path" ] == "src/half.py"
        assert d[ "checks_run" ][ 0 ][ "passed" ] is False and d[ "checks_not_run" ] == []
        assert h.checks.ran == [ CHECK ]  # partial edit was checked; NOTES.md was never written
    finally:
        h.close()


def test_a_retried_write_task_is_not_run_again() -> None:
    first = WriteHarness( write_gateway(), WritingExecutor({ "NOTES.md": "# one\n" }))
    try:
        report = first.run.run_one()               # ran; then the gateway hands the same task out again
        assert report.outcome is OutcomeKind.SUCCEEDED and first.gateway.completed[ "ok" ] is True
        g2 = write_gateway()
        g2._claim[ "attempt" ] = 2
        second = WriteHarness( g2, WritingExecutor({ "NOTES.md": "# two\n" }), base=first.base )
        second.journal = first.journal
        report = second.run.run_one()
        assert second.writer.seen == [], "the same task id never executes twice"
        d = g2.completed[ "details" ]
        assert d[ "resumed" ] is True and g2.completed[ "ok" ] is False
        assert "not run again" in g2.completed[ "summary" ]
        assert ( first.root / "NOTES.md" ).read_text() == "# one\n"
    finally:
        first.close()


def test_secret_and_oversized_files_are_not_sent_as_complete() -> None:
    big = "line\n" * 20_000
    files = { "creds.md": "api_key = 'abcdefghijklmnop1234'\n", "big.md": big, "ok.md": "fine\n" }
    h = WriteHarness( write_gateway(), WritingExecutor( files ), checks=[])
    try:
        h.run.run_one()
        d = h.gateway.completed[ "details" ]
        arts = { a[ "path" ]: a for a in d[ "artifacts" ]}
        assert arts[ "creds.md" ][ "content" ] is None and "credential" in arts[ "creds.md" ][ "note" ]
        assert arts[ "big.md" ][ "content" ] is None and not arts[ "big.md" ][ "complete" ]
        assert arts[ "big.md" ][ "size" ] == len( big )
        assert arts[ "ok.md" ][ "complete" ] and arts[ "ok.md" ][ "content" ] == "fine\n"
        assert d[ "diff" ][ "diff" ] == "" and "withheld" in d[ "diff" ][ "diff_note" ]
        assert "abcdefghijklmnop1234" not in json.dumps( d )
        assert "no checks are configured" in d[ "checks_not_run" ][ 0 ]
    finally:
        h.close()


def test_a_busy_workspace_refuses_a_second_write() -> None:
    h = WriteHarness( write_gateway(), WritingExecutor({ "NOTES.md": "x" }))
    try:
        other = FlockWorkspaceLock( h.base / "journal" / "locks" )
        other.acquire( "bridge_sandbox" )
        try:
            report = h.run.run_one()
        finally:
            other.release( "bridge_sandbox" )
        assert report.outcome is OutcomeKind.REFUSED and "another write task" in report.summary
        assert h.writer.seen == []
        again = FlockWorkspaceLock( h.base / "journal" / "locks" )
        again.acquire( "bridge_sandbox" )            # the run released its own lock
        again.release( "bridge_sandbox" )
    finally:
        h.close()


def test_an_unsettled_earlier_write_blocks_the_next() -> None:
    h = WriteHarness( write_gateway(), WritingExecutor({ "NOTES.md": "x" }))
    try:
        h.journal.mark_unsettled( "bridge_sandbox", "still-running" )
        h._settled = lambda local: False
        h.session._settled = h._settled
        report = h.run.run_one()
        assert report.outcome is OutcomeKind.REFUSED and "may still be running" in report.summary
        assert h.writer.seen == []
    finally:
        h.close()


# -- the wire ----------------------------------------------------------------------------

def test_http_complete_sends_details_and_marks_truncation() -> None:
    gw   = HttpTaskGateway( "https://bridge.example/api", "W" * 40 )
    sent = {}
    gw._call = lambda method, path, payload=None: ( sent.update( payload or {}), ( 200, { "state": "succeeded" }))[ 1 ]
    t = task()
    assert gw.complete( t, True, "s", "b", "p", { "outcome": "changed" }) == "succeeded"
    assert sent[ "details" ] == { "outcome": "changed" }
    sent.clear()
    gw.complete( t, True, "s", "b", "p" )
    assert "details" not in sent
    long = clip( "x" * 70_000, 65536 )
    assert len( long ) <= 65536 and "[truncated: the first" in long and "of 70000 characters" in long


def test_scanner_names_kinds_never_values() -> None:
    s = SecretScanner( known=[ "Z" * 30 ])
    assert s.found( "nothing here" ) == []
    assert "a bridge token" in s.found( "token " + "Z" * 30 )
    assert "private key" in s.found( "-----BEGIN OPENSSH PRIVATE KEY-----" )


def test_journal_and_lock_basics() -> None:
    base = Path( tempfile.mkdtemp())
    try:
        j = JsonWriteJournal( base / "j" )
        assert j.load( "t1" ) is None
        j.save( "t1", { "a": 1 })
        assert j.load( "t1" ) == { "a": 1 }
        assert oct(( base / "j" ).stat().st_mode & 0o777 ) == "0o700"
        lock = FlockWorkspaceLock( base / "l" )
        lock.acquire( "w" )
        try:
            FlockWorkspaceLock( base / "l" ).acquire( "w" )
            raise AssertionError( "a second lock was granted" )
        except WriteBlocked:
            pass
        lock.release( "w" )
    finally:
        shutil.rmtree( base )


TESTS = [ v for k, v in dict( globals()).items() if k.startswith( "test_" )]

if __name__ == "__main__":
    for test in TESTS:
        test()
        print( "ok", test.__name__ )
    print( f"{ len( TESTS )} passed" )
