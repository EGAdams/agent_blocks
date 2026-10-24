#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "lancedb", "pyarrow"]
# ///
"""ADW Memory Compact — prune aged turns out of the shared LanceDB memory
store and reclaim the space those deletions leave behind, without ever
dropping or truncating a table.

The receptionist's shared memory (lancedb_memory/lance_data/*.lance) only
grows: every turn Toyota or Mazda handles is embedded and added, and nothing
in the read path ever removes a row. `LanceDBMemoryStore.forget()` can delete
specific ids, but nothing walks the table by AGE, and `agent.clear_context()`
(the REPL's "/compact") only rotates the session id forward — it reduces one
session's live context, not the table on disk. This script is the missing
maintenance operation: delete turns older than a cutoff, then ask LanceDB to
physically reclaim the fragments and old versions that `.delete()` alone
leaves behind (`Table.optimize()`), which is what "compact" means for a Lance
table. It never calls anything resembling `drop_table` or an unfiltered
`.delete()` — see `survey()` below, which refuses a cutoff that would remove
every row in a table rather than let a compaction silently become a clear.

Losing a row here is not losing the memory: `NexusArchiveObserver` already
wrote every turn's raw text to lancedb_memory/nexus/<id>.json independently of
LanceDB (see agent_memory/observers/nexus_archive_observer.py), so pruning the
vector table only shrinks what recall searches, never the portable record.

Three phases, all `kind="code"` — deciding "older than N days" and running the
delete/optimize calls needs no judgement, so no agent is named and the config
validates against an empty agent roster.

Usage:
    uv run adws/adw_memory_compact.py "<why this compaction is being run>" \\
        [--lancedb-uri /home/adamsl/agent_blocks/lancedb_memory/lance_data] \\
        [--table conversation_memory_hash256] [--older-than-days 90] \\
        [--dry-run] [--message-out /tmp/line.txt] \\
        [--config adws/adw_sssf_config/sssf.config.yaml] [--adw-id a1b2c3d4]

Phases: engineer(request) -> code(survey) -> code(purge) -> code(compact)
"""

from __future__ import annotations

import argparse
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from adw_modules.data_types import PhaseParams
from adw_modules.runner import Run
from sssf_roles import (ConsoleLifecycleLog, FileOrInlinePromptResolver, IApplication,
                        IArgumentParser, ILifecycleLog, IPhase, IPhaseExecutor,
                        IPromptResolver, LifecycleLogged, ManualContextPhaseExecutor,
                        PrintLifecycleLog, SessionRunFactory, SssfConfigLoader)

REQUIRED_AGENTS: list[str] = []          # code-only: no judgement call in this chain
DEFAULT_LANCEDB_URI = "/home/adamsl/agent_blocks/lancedb_memory/lance_data"
DEFAULT_OLDER_THAN_DAYS = 90              # a quarter of quiet rows is safe to prune


# ─────────────────────────────────────────────────────────────────────────
# Values. Plain data; no behavior, no interface needed.
# ─────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CompactRequest:
    """Parsed command-line intent for one compaction pass."""
    prompt: str
    lancedb_uri: str
    table: str                 # "" means every table found at lancedb_uri
    older_than_days: int
    dry_run: bool
    config: str
    adw_id: str | None
    message_out: str


@dataclass(frozen=True)
class TableSnapshot:
    """One table's physical shape at a point in time."""
    name: str
    total_rows: int
    total_bytes: int
    num_fragments: int


@dataclass(frozen=True)
class SurveyResult:
    """What a dry read of every target table found, before anything is touched."""
    cutoff_iso: str
    before: dict[str, TableSnapshot] = field(default_factory=dict)
    matched: dict[str, int] = field(default_factory=dict)     # rows older than cutoff
    # Tables where the age filter matches every row -- pruning them would be a
    # clear wearing a compaction's name, so purge() must skip these, not touch them.
    refused: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PurgeResult:
    removed: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class CompactResult:
    after: dict[str, TableSnapshot] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────
# Roles (interfaces). The one new role this script needs beyond sssf_roles —
# everything that touches lancedb goes through it, so the phases below never
# import lancedb themselves.
# ─────────────────────────────────────────────────────────────────────────

class ITableMaintenance(ABC):
    """Age-based maintenance over LanceDB tables. Nothing else here knows lancedb."""

    @abstractmethod
    def list_tables(self, uri: str, only: str) -> list[str]: ...

    @abstractmethod
    def snapshot(self, uri: str, table: str) -> TableSnapshot: ...

    @abstractmethod
    def count_older_than(self, uri: str, table: str, cutoff: datetime) -> int: ...

    @abstractmethod
    def purge_older_than(self, uri: str, table: str, cutoff: datetime) -> int: ...

    @abstractmethod
    def compact(self, uri: str, table: str) -> None: ...


class LanceDBTableMaintenance(LifecycleLogged, ITableMaintenance):
    """The only class in this file allowed to import lancedb.

    Mirrors the one-module rule agent_memory/adapters/lancedb_store.py already
    follows for the receptionist's own read/write path -- this is the same rule
    applied to the maintenance path, in a script that does not import that
    package at all (an ADW stays self-contained; it does not reach into
    lancedb_memory's internals to do its job).
    """

    def __init__(self, logger: ILifecycleLog) -> None:
        LifecycleLogged.__init__(self, logger)

    def _open(self, uri: str, table: str):
        import lancedb
        db = lancedb.connect(uri)
        return db.open_table(table)

    def list_tables(self, uri: str, only: str) -> list[str]:
        import lancedb
        db = lancedb.connect(uri)
        names = sorted(db.table_names())
        if only:
            if only not in names:
                raise ValueError(f"table {only!r} not found at {uri} (have: {', '.join(names)})")
            return [only]
        return names

    def snapshot(self, uri: str, table: str) -> TableSnapshot:
        t = self._open(uri, table)
        stats = t.stats()
        fragments = stats.get("fragment_stats", {}) or {}
        return TableSnapshot(
            name=table,
            total_rows=stats.get("num_rows", t.count_rows()),
            total_bytes=stats.get("total_bytes", 0),
            num_fragments=fragments.get("num_fragments", 0),
        )

    def count_older_than(self, uri: str, table: str, cutoff: datetime) -> int:
        t = self._open(uri, table)
        rows = t.search().where(f"time < {cutoff.timestamp()}").limit(0).to_list()
        return len(rows)

    def purge_older_than(self, uri: str, table: str, cutoff: datetime) -> int:
        t = self._open(uri, table)
        before = t.count_rows()
        t.delete(f"time < {cutoff.timestamp()}")
        return before - t.count_rows()

    def compact(self, uri: str, table: str) -> None:
        t = self._open(uri, table)
        t.optimize()


# ─────────────────────────────────────────────────────────────────────────
# Phases. Each is the smallest IPhase that earns its keep: survey never
# mutates, purge only deletes rows the survey already proved are safe, and
# compact only reclaims space purge already freed.
# ─────────────────────────────────────────────────────────────────────────

class RequestCapturePhase(LifecycleLogged, IPhase):
    def __init__(self, engineer: str, request: CompactRequest, logger: ILifecycleLog) -> None:
        LifecycleLogged.__init__(self, logger)
        self._engineer = engineer
        self._request = request

    @property
    def params(self) -> PhaseParams:
        return PhaseParams(name="request", kind="engineer", owner=self._engineer,
                          description="Record which LanceDB tables are being compacted and under what retention")

    def act(self, handle) -> None:
        request = self._request
        handle.log(input=request.prompt, lancedb_uri=request.lancedb_uri,
                  table=request.table or "(all tables)", older_than_days=request.older_than_days,
                  dry_run=request.dry_run)


class SurveyPhase(LifecycleLogged, IPhase):
    """Reads every target table's size and how many rows the cutoff would hit.
    Mutates nothing -- this is the gate that decides purge() is safe to run."""

    def __init__(self, maintenance: ITableMaintenance, request: CompactRequest, logger: ILifecycleLog) -> None:
        LifecycleLogged.__init__(self, logger)
        self._maintenance = maintenance
        self._request = request

    @property
    def params(self) -> PhaseParams:
        return PhaseParams(name="survey", kind="code", owner="memory",
                          description="Measure each table and count rows older than the cutoff, before touching anything")

    def act(self, handle) -> SurveyResult:
        request = self._request
        cutoff = datetime.now(timezone.utc) - timedelta(days=request.older_than_days)
        tables = self._maintenance.list_tables(request.lancedb_uri, request.table)

        before: dict[str, TableSnapshot] = {}
        matched: dict[str, int] = {}
        refused: list[str] = []
        for name in tables:
            snap = self._maintenance.snapshot(request.lancedb_uri, name)
            hits = self._maintenance.count_older_than(request.lancedb_uri, name, cutoff)
            before[name] = snap
            matched[name] = hits
            # A cutoff that matches every row would compact a table into nothing --
            # that is a clear, not a compaction, so this table is left untouched.
            if snap.total_rows > 0 and hits >= snap.total_rows:
                refused.append(name)

        handle.log(cutoff=cutoff.isoformat(), tables=", ".join(tables) or "(none)",
                  rows_before={n: s.total_rows for n, s in before.items()},
                  matched=matched, refused=", ".join(refused) or "(none)")
        return SurveyResult(cutoff_iso=cutoff.isoformat(), before=before, matched=matched, refused=refused)


class PurgePhase(LifecycleLogged, IPhase):
    """Deletes rows the survey already proved are older than the cutoff AND
    not the whole table. Does nothing on a dry run."""

    def __init__(self, maintenance: ITableMaintenance, request: CompactRequest,
                 survey: SurveyResult, logger: ILifecycleLog) -> None:
        LifecycleLogged.__init__(self, logger)
        self._maintenance = maintenance
        self._request = request
        self._survey = survey

    @property
    def params(self) -> PhaseParams:
        return PhaseParams(name="purge", kind="code", owner="memory",
                          description="Delete the rows the survey found to be older than the cutoff, skipping any table that would be emptied")

    def act(self, handle) -> PurgeResult:
        request, survey = self._request, self._survey
        cutoff = datetime.fromisoformat(survey.cutoff_iso)
        removed: dict[str, int] = {}
        for name, hits in survey.matched.items():
            if name in survey.refused or hits == 0:
                removed[name] = 0
                continue
            if request.dry_run:
                removed[name] = hits   # what WOULD be removed; nothing deleted
                continue
            removed[name] = self._maintenance.purge_older_than(request.lancedb_uri, name, cutoff)

        handle.log(dry_run=request.dry_run, removed=removed,
                  refused=", ".join(survey.refused) or "(none)")
        return PurgeResult(removed=removed)


class CompactPhase(LifecycleLogged, IPhase):
    """Reclaims the space purge's deletions left behind. Skipped on a dry run,
    since there is nothing yet to reclaim."""

    def __init__(self, maintenance: ITableMaintenance, request: CompactRequest,
                 survey: SurveyResult, purge: PurgeResult, logger: ILifecycleLog) -> None:
        LifecycleLogged.__init__(self, logger)
        self._maintenance = maintenance
        self._request = request
        self._survey = survey
        self._purge = purge

    @property
    def params(self) -> PhaseParams:
        return PhaseParams(name="compact", kind="code", owner="memory",
                          description="Run LanceDB's own compaction so deleted rows stop occupying space on disk")

    def act(self, handle) -> CompactResult:
        request, survey, purge = self._request, self._survey, self._purge
        after: dict[str, TableSnapshot] = {}
        for name in survey.before:
            if not request.dry_run and purge.removed.get(name, 0) > 0:
                self._maintenance.compact(request.lancedb_uri, name)
            after[name] = self._maintenance.snapshot(request.lancedb_uri, name)

        handle.log(dry_run=request.dry_run,
                  bytes_before={n: s.total_bytes for n, s in survey.before.items()},
                  bytes_after={n: s.total_bytes for n, s in after.items()})
        return CompactResult(after=after)


# ─────────────────────────────────────────────────────────────────────────
# Adapters not already covered by sssf_roles.
# ─────────────────────────────────────────────────────────────────────────

class ArgparseCompactArgumentParser(LifecycleLogged, IArgumentParser):
    def __init__(self, description: str, logger: ILifecycleLog) -> None:
        LifecycleLogged.__init__(self, logger)
        self._description = description

    def parse(self, argv: list[str]) -> CompactRequest:
        parser = argparse.ArgumentParser(description=self._description)
        parser.add_argument("prompt", help="inline text or a path to a prompt file")
        parser.add_argument("--lancedb-uri", default=DEFAULT_LANCEDB_URI,
                            help="the LanceDB directory to compact")
        parser.add_argument("--table", default="",
                            help="compact only this table; default is every table at --lancedb-uri")
        parser.add_argument("--older-than-days", type=int, default=DEFAULT_OLDER_THAN_DAYS,
                            help="rows older than this many days are candidates for removal")
        parser.add_argument("--dry-run", action="store_true",
                            help="report what would be removed/reclaimed; delete and optimize nothing")
        parser.add_argument("--message-out", default="",
                            help="write the operator's one line here, for a non-terminal caller")
        parser.add_argument("--config", default="adws/adw_sssf_config/sssf.config.yaml")
        parser.add_argument("--adw-id", default=None, help="join or pin an existing session")
        parsed = parser.parse_args(argv)
        if parsed.older_than_days < 1:
            parser.error("--older-than-days must be at least 1 -- 0 would match everything, which is a clear")
        return CompactRequest(prompt=parsed.prompt, lancedb_uri=parsed.lancedb_uri, table=parsed.table,
                             older_than_days=parsed.older_than_days, dry_run=parsed.dry_run,
                             config=parsed.config, adw_id=parsed.adw_id, message_out=parsed.message_out)


# ─────────────────────────────────────────────────────────────────────────
# Composition root. The only place that is allowed to know every interface
# AND every concrete adapter.
# ─────────────────────────────────────────────────────────────────────────

class MemoryCompactApplication(LifecycleLogged, IApplication):
    def __init__(self,
                 argument_parser,
                 prompt_resolver: IPromptResolver,
                 config_loader,
                 session_factory,
                 phase_executor: IPhaseExecutor,
                 maintenance: ITableMaintenance,
                 logger: ILifecycleLog) -> None:
        LifecycleLogged.__init__(self, logger)
        self._argument_parser = argument_parser
        self._prompt_resolver = prompt_resolver
        self._config_loader = config_loader
        self._session_factory = session_factory
        self._phase_executor = phase_executor
        self._maintenance = maintenance

    def run(self, argv: list[str]) -> int:
        request = self._argument_parser.parse(argv)
        prompt = self._prompt_resolver.resolve(request.prompt)
        request = CompactRequest(**{**request.__dict__, "prompt": prompt})

        cfg = self._config_loader.load(request.config)
        self._config_loader.validate(cfg, REQUIRED_AGENTS)
        run: Run = self._session_factory.create(cfg, request.adw_id)

        session_log: ILifecycleLog = ConsoleLifecycleLog(run)

        self._phase_executor.execute(run, RequestCapturePhase(run.engineer, request, session_log))
        survey: SurveyResult = self._phase_executor.execute(
            run, SurveyPhase(self._maintenance, request, session_log))
        purge: PurgeResult = self._phase_executor.execute(
            run, PurgePhase(self._maintenance, request, survey, session_log))
        compact: CompactResult = self._phase_executor.execute(
            run, CompactPhase(self._maintenance, request, survey, purge, session_log))

        message = self._compose_message(request, survey, purge)
        if request.message_out:
            Path(request.message_out).write_text(message + "\n")

        # Refusing to empty a table is a correct outcome, not a crash -- but the
        # operator still needs to see it, the same way adw_map_open's "not
        # accepted" line surfaces a resolved-but-unusable map link.
        accepted = not survey.refused
        reason = (f"refused to compact {', '.join(survey.refused)} -- the cutoff would remove every row"
                 if survey.refused else "")
        return run.finish(accepted=accepted, reason=reason)

    def _compose_message(self, request: CompactRequest, survey: SurveyResult, purge: PurgeResult) -> str:
        total_removed = sum(purge.removed.values())
        verb = "would remove" if request.dry_run else "removed"
        parts = [f"{name}: {verb} {count} row(s) older than {request.older_than_days}d"
                for name, count in purge.removed.items()]
        line = "; ".join(parts) if parts else "no tables matched"
        if survey.refused:
            line += f"; skipped (would clear): {', '.join(survey.refused)}"
        prefix = "memory compact (dry run)" if request.dry_run else "memory compact"
        return f"{prefix}: {line} [{total_removed} total]"


def build_application() -> IApplication:
    """Wire the concrete adapters into the application. The one spot that may."""
    bootstrap_log: ILifecycleLog = PrintLifecycleLog()
    return MemoryCompactApplication(
        argument_parser = ArgparseCompactArgumentParser(__doc__, bootstrap_log),
        prompt_resolver = FileOrInlinePromptResolver(bootstrap_log),
        config_loader   = SssfConfigLoader(bootstrap_log),
        session_factory = SessionRunFactory(bootstrap_log),
        phase_executor  = ManualContextPhaseExecutor(bootstrap_log),
        maintenance     = LanceDBTableMaintenance(bootstrap_log),
        logger          = bootstrap_log,
    )


if __name__ == "__main__":
    sys.exit(build_application().run(sys.argv[1:]))
