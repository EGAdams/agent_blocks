"""The operations that only look: projects, status, health, logs, history. None of them changes anything."""
from __future__ import annotations

from pathlib import Path

from ..models import DeployRequest, OperationResult, Project
from .common import Deps, Refused, diff, health, result, root_of


class Projects:
    name, writes, needs_project = "projects", False, False

    def run(self, deps: Deps, req: DeployRequest, project: Project | None) -> OperationResult:
        rows = []
        for name in deps.catalog.names():
            p = deps.catalog.get(name)
            rows.append({"project": p.name, "description": p.description, "source": p.source,
                         "units": p.units + p.self_units, "checks": len(p.checks),
                         "health": [c.name for c in p.health]})
        return result(True, True, f"{len(rows)} projects: " + ", ".join(r["project"] for r in rows), projects=rows)


class Health:
    name, writes, needs_project = "health", False, True

    def run(self, deps: Deps, req: DeployRequest, project: Project) -> OperationResult:
        ok, report = health(deps, project, wait=False)
        failing = [r["check"] for r in report if not r["ok"]]
        line = (f"{project.name} is healthy ({len(report)} checks pass)" if ok
                else f"{project.name} is NOT healthy: {', '.join(failing)} failing")
        return result(ok, True, line, checks=report)


class Status:
    name, writes, needs_project = "status", False, True

    def run(self, deps: Deps, req: DeployRequest, project: Project) -> OperationResult:
        root = root_of(project)
        latest = deps.releases.latest(project.name)
        units = {u: deps.services.state(u) for u in project.units + project.self_units}
        data: dict = {"project": project.name, "source": project.source, "head": deps.tree.head(root),
                      "units": units, "latest_release": latest.release_id if latest else None,
                      "latest_release_at": latest.created_at if latest else None}
        if project.source == "in_place":
            current = deps.snapshots.scan(project, root)
            pending = diff(latest.files, current) if latest else [p for p in deps.tree.dirty(root) if project.covers(p)]
            data["pending"] = pending[:60]
            data["pending_count"] = len(pending)
            waiting = f"{len(pending)} changed file(s) not yet deployed" if pending else "nothing waiting to deploy"
        elif project.source == "git_pull":
            remote = deps.tree.fetch(root, project.remote, project.branch)
            behind = [] if remote == data["head"] else deps.tree.changed(root, data["head"] or remote, remote)
            data.update(remote_head=remote, pending=behind[:60], pending_count=len(behind))
            waiting = (f"{project.remote}/{project.branch} is ahead by {len(behind)} changed file(s)" if remote != data["head"]
                       else f"up to date with {project.remote}/{project.branch}")
        else:
            waiting = "not deployable from here (health and logs only)"
        down = [u for u, s in units.items() if s != "active"]
        line = f"{project.name}: {waiting}; " + (f"not active: {', '.join(down)}" if down else f"{len(units)} unit(s) active")
        return result(True, True, line, **data)


class Logs:
    name, writes, needs_project = "logs", False, True

    def run(self, deps: Deps, req: DeployRequest, project: Project) -> OperationResult:
        allowed = project.units + project.self_units + project.log_units + list(project.log_files)
        unit = req.unit or (allowed[0] if allowed else "")
        if unit not in allowed:
            raise Refused(f"{project.name}'s logs come from: {', '.join(allowed) or 'nothing'}")
        lines = max(1, min(400, req.lines))
        if unit in project.log_files:
            text = deps.logs.tail_file(Path(project.log_files[unit]).expanduser(), lines)
        else:
            text = deps.logs.tail(unit, lines)
        return result(True, True, f"the last {lines} lines of {unit}", unit=unit, text=text)


class History:
    name, writes, needs_project = "history", False, True

    def run(self, deps: Deps, req: DeployRequest, project: Project) -> OperationResult:
        limit = max(1, min(50, req.lines if req.lines != 80 else 15))
        runs = [e.model_dump() for e in deps.ledger.recent(project.name, limit)]
        releases = [{"release_id": r.release_id, "kind": r.kind, "created_at": r.created_at, "head": r.head,
                     "adw_id": r.adw_id, "task_id": r.task_id, "reason": r.reason, "previous": r.previous}
                    for r in deps.releases.recent(project.name, limit)]
        return result(True, True, f"{project.name}: {len(runs)} recent run(s), {len(releases)} release(s)",
                      runs=runs, releases=releases)
