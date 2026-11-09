"""The Deployment ADW's data: what a project is, what a release records, what a run answers.

Everything that crosses a boundary (the project table on disk, a release on disk, the ledger, the ADW's
one JSON outcome) is a pydantic model, so a malformed file is refused when it is read, not halfway
through a deploy.
"""
from __future__ import annotations

import fnmatch
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, Field

OPERATIONS = ("projects", "status", "health", "logs", "history", "deploy", "restart", "rollback")
#: Operations that change something. Everything else only reads.
WRITES = frozenset({"deploy", "restart", "rollback"})
#: Never part of a release, wherever they are.
SKIP_DIRS = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache"})


class HealthCheck(BaseModel):
    """One thing that must be true for a project to be healthy."""
    name: str
    kind: Literal["http", "unit", "file_fresh"]
    url: str | None = None                  # http
    expect_status: int = 200                # http
    contains: str | None = None             # http: a substring the body must hold
    unit: str | None = None                 # unit: a systemd --user unit that must be active
    path: str | None = None                 # file_fresh: a file that must have been written recently
    max_age_seconds: int = 900              # file_fresh


class CacheBust(BaseModel):
    """A page that names its stylesheets with ?v=<tag>. A deployed stylesheet gets a new tag, so an open
    browser fetches it instead of reusing yesterday's copy."""
    html: str                               # the page, relative to the project root
    url_root: str                           # the part of a file's path the page leaves out ("dashboard/")


class Project(BaseModel):
    """One deployable thing, configured in projects.json -- never named by a request."""
    name: str
    description: str
    root: str                               # a git work tree; "~" is expanded
    source: Literal["in_place", "git_pull", "none"]
    #: in_place  -- the change is already in the work tree (the Bridge's coding agents write there);
    #:              deploying checks it, records it as a release and makes it live.
    #: git_pull  -- the change is a commit on `remote/branch`; deploying fast-forwards to it.
    #: none      -- nothing here can change it; health and logs only.
    scope: list[str] = Field(default_factory=list)          # directories (relative, ending "/") a release covers
    suffixes: list[str] = Field(default_factory=list)       # file types a release covers
    exclude: list[str] = Field(default_factory=list)        # glob patterns left out (runtime data, backups)
    remote: str = "origin"
    branch: str = "main"
    checks: list[list[str]] = Field(default_factory=list)   # argv lists, run in `root`; all must pass
    check_timeout_seconds: int = 600
    units: list[str] = Field(default_factory=list)          # restarted by `restart`, and by `deploy` when needed
    self_units: list[str] = Field(default_factory=list)     # units the worker itself runs in: restarted a few
                                                            # seconds later, after the task has answered
    restart_when: list[str] = Field(default_factory=list)   # globs: a deployed change matching one restarts `units`
    log_units: list[str] = Field(default_factory=list)      # extra units whose logs may be read
    log_files: dict[str, str] = Field(default_factory=dict) # name -> log file, for a unit that writes its own
    health: list[HealthCheck] = Field(default_factory=list)
    health_wait_seconds: int = 30
    cache_bust: CacheBust | None = None

    def covers(self, rel: str) -> bool:
        """Is this file (a path relative to the root) part of what a release records?"""
        path = PurePosixPath(rel)
        if not any(s in ("", "./") or rel.startswith(s) for s in self.scope or [""]):
            return False
        if self.suffixes and path.suffix not in self.suffixes:
            return False
        if any(part in SKIP_DIRS for part in path.parts):
            return False
        return not any(fnmatch.fnmatch(rel, pattern) for pattern in self.exclude)


class Catalog(BaseModel):
    projects: list[Project]


class Release(BaseModel):
    """A state of a project that was live: its git commit and the content hash of every file in scope."""
    release_id: str
    project: str
    created_at: str
    kind: Literal["deploy", "pre_rollback", "rollback"]
    head: str | None = None
    files: dict[str, str] = Field(default_factory=dict)     # path -> sha256 (in_place only)
    adw_id: str
    task_id: str = ""
    reason: str = ""
    previous: str | None = None


class DeployRequest(BaseModel):
    """One run of the ADW. Built from command-line flags, which the worker builds from a checked table."""
    operation: str
    project: str = ""
    reason: str = ""
    release_id: str = ""
    unit: str = ""
    lines: int = 80
    dry_run: bool = False
    task_id: str = ""
    client: str = ""


class Step(BaseModel):
    at: str
    name: str
    ok: bool = True
    detail: dict = Field(default_factory=dict)


class OperationResult(BaseModel):
    """The ADW's one answer. `verified` means the ADW checked the result itself, never that it was queued."""
    ok: bool
    verified: bool
    line: str
    data: dict = Field(default_factory=dict)


class LedgerEntry(BaseModel):
    at: str
    adw_id: str
    task_id: str = ""
    client: str = ""
    project: str
    operation: str
    ok: bool
    verified: bool
    line: str
    release_id: str | None = None
    dry_run: bool = False
