"""What the operations share: the injected interfaces, and the few steps more than one operation takes."""
from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path

from ..interfaces import (IClock, ICheckRunner, IHealthProbe, ILedger, ILogReader, IProjectCatalog, IProjectLock,
                          IReleaseStore, IServiceManager, ISnapshotter, ISourceTree, ITrace)
from ..models import DeployRequest, OperationResult, Project, Release

#: How long after answering a self-unit restart fires: enough for the worker to publish this task's result.
SELF_RESTART_SECONDS = 8


@dataclass(frozen=True)
class Deps:
    """The interfaces an operation may use. A plain carrier of live objects, not a wire contract."""
    catalog: IProjectCatalog
    tree: ISourceTree
    snapshots: ISnapshotter
    releases: IReleaseStore
    services: IServiceManager
    probe: IHealthProbe
    logs: ILogReader
    checks: ICheckRunner
    ledger: ILedger
    trace: ITrace
    lock: IProjectLock
    clock: IClock
    adw_id: str


class Refused(ValueError):
    """The request cannot be carried out as asked; nothing was changed."""


def root_of(project: Project) -> Path:
    return Path(os.path.expanduser(project.root))


def release_id(deps: Deps) -> str:
    stamp = deps.clock.now()[:19].replace("-", "").replace(":", "")
    return f"{stamp}-{deps.adw_id}"


def diff(old: dict[str, str], new: dict[str, str]) -> list[str]:
    return sorted(p for p in set(old) | set(new) if old.get(p) != new.get(p))


def record(deps: Deps, project: Project, kind: str, req: DeployRequest, previous: str | None,
           head: str | None = None) -> Release:
    root = root_of(project)
    files = deps.snapshots.scan(project, root) if project.source == "in_place" else {}
    release = Release(release_id=release_id(deps) + ("" if kind != "pre_rollback" else "-pre"), project=project.name,
                      created_at=deps.clock.now(), kind=kind, head=head or deps.tree.head(root), files=files,
                      adw_id=deps.adw_id, task_id=req.task_id, reason=req.reason[:300], previous=previous)
    deps.releases.save(release, root)
    deps.trace.step("release_recorded", release_id=release.release_id, kind=kind, files=len(files), head=release.head)
    return release


def needs_restart(project: Project, changed: list[str]) -> bool:
    return any(fnmatch.fnmatch(p, g) for p in changed for g in project.restart_when)


def restart(deps: Deps, project: Project, units: list[str]) -> dict[str, str]:
    """Restart now, except a unit this worker runs in: that one is scheduled, so the answer gets out first."""
    done: dict[str, str] = {}
    for unit in units:
        if unit in project.self_units:
            deps.services.restart_later(unit, SELF_RESTART_SECONDS, deps.adw_id)
            done[unit] = f"scheduled in {SELF_RESTART_SECONDS}s"
        else:
            deps.services.restart(unit)
            done[unit] = "restarted"
    deps.trace.step("restart", units=done)
    return done


def health(deps: Deps, project: Project, wait: bool) -> tuple[bool, list[dict]]:
    """Every configured check, retried until they all pass or the project's wait runs out. A check on a unit
    whose restart is only scheduled is skipped here: it would be judging the process about to be replaced."""
    checks = [c for c in project.health if not (c.kind == "unit" and c.unit in project.self_units and wait)]
    deadline = deps.clock.monotonic() + (project.health_wait_seconds if wait else 0)
    while True:
        report = []
        for check in checks:
            ok, said = deps.probe.check(check)
            report.append({"check": check.name, "ok": ok, "detail": said})
        healthy = all(r["ok"] for r in report)
        if healthy or deps.clock.monotonic() >= deadline:
            deps.trace.step("health", ok=healthy, checks=report)
            return healthy, report
        deps.clock.sleep(2)


def restore(deps: Deps, project: Project, target: Release) -> list[str]:
    """Put the project back exactly as `target` recorded it. In place: every file in scope gets the recorded
    bytes (a file the release did not have is removed). From git: HEAD moves back with --keep."""
    root = root_of(project)
    if project.source == "git_pull":
        if target.head and target.head != deps.tree.head(root):
            changed = deps.tree.changed(root, target.head, deps.tree.head(root) or target.head)
            deps.tree.reset_keep(root, target.head)
            deps.trace.step("reset_keep", head=target.head, files=len(changed))
            return changed
        return []
    current = deps.snapshots.scan(project, root)
    changed = diff(current, target.files)
    for rel in changed:
        path = root / rel
        want = target.files.get(rel)
        if want is None:
            path.unlink(missing_ok=True)
            continue
        data = deps.releases.content(want)
        mode = path.stat().st_mode & 0o777 if path.exists() else 0o664
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.adw-deploy-{deps.adw_id}")
        tmp.write_bytes(data)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    deps.trace.step("restore", release_id=target.release_id, files=changed[:200], count=len(changed))
    return changed


def result(ok: bool, verified: bool, line: str, **data) -> OperationResult:
    return OperationResult(ok=ok, verified=verified, line=line, data=data)
