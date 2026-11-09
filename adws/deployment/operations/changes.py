"""The operations that change something: deploy, restart, rollback. Each holds the project's lock, records
every step, and judges its own result with the project's health checks -- `ok` is never "it was started".

deploy   in_place: the change is already in the work tree. Run the checks; if they pass, give changed
                   stylesheets a new ?v= tag, record the release, restart what the change needs, and check
                   health. If health fails, the previous release is put back and the answer says so.
         git_pull: fetch; fast-forward only; run the checks (on failure HEAD goes back with --keep);
                   record the release, restart, check health (on failure, back to the old commit).
restart  the project's units (or one of them), then health.
rollback a recorded release (default: the one before the live one). The state being replaced is
         recorded first, so a rollback can itself be undone.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from ..models import DeployRequest, OperationResult, Project, Release
from .common import Deps, Refused, diff, health, needs_restart, record, restart, restore, result, root_of


def run_checks(deps: Deps, project: Project) -> tuple[bool, list[dict]]:
    report = []
    for argv in project.checks:
        ok, tail = deps.checks.run(argv, root_of(project), project.check_timeout_seconds)
        report.append({"check": " ".join(argv)[:200], "ok": ok, "output_tail": tail[-1500:]})
        deps.trace.step("check", ok=ok, argv=argv)
        if not ok:
            break
    return all(r["ok"] for r in report), report


def bust_cache(deps: Deps, project: Project, changed: list[str]) -> list[str]:
    """A changed stylesheet or script the page names with ?v=... gets this run's tag, so open browsers
    load the new one. Only the ?v= value of a URL the change touched is rewritten."""
    spec = project.cache_bust
    if spec is None:
        return []
    html = root_of(project) / spec.html
    if not html.is_file():
        return []
    text, bumped = html.read_text(encoding="utf-8"), []
    for rel in changed:
        if not rel.startswith(spec.url_root) or Path(rel).suffix not in (".css", ".js"):
            continue
        url = "/" + rel[len(spec.url_root):]
        text, count = re.subn(re.escape(url) + r"\?v=[A-Za-z0-9._-]+", f"{url}?v=adw{deps.adw_id}", text)
        if count:
            bumped.append(url)
    if bumped:
        mode = html.stat().st_mode & 0o777
        tmp = html.with_name(f".{html.name}.adw-deploy-{deps.adw_id}")
        tmp.write_text(text, encoding="utf-8")
        os.chmod(tmp, mode)
        os.replace(tmp, html)
    deps.trace.step("cache_bust", urls=bumped)
    return bumped


class Deploy:
    name, writes, needs_project = "deploy", True, True

    def run(self, deps: Deps, req: DeployRequest, project: Project) -> OperationResult:
        if project.source == "none":
            raise Refused(f"{project.name} is not deployable from here; health and logs only")
        if not req.reason.strip() and not req.dry_run:      # a preview records nothing, so needs no reason
            raise Refused("say what is being deployed (reason), so the record shows why")
        with deps.lock.hold(project.name):
            if project.source == "in_place":
                return self._in_place(deps, req, project)
            return self._git_pull(deps, req, project)

    def _in_place(self, deps: Deps, req: DeployRequest, project: Project) -> OperationResult:
        root = root_of(project)
        latest = deps.releases.latest(project.name)
        current = deps.snapshots.scan(project, root)
        changed = diff(latest.files, current) if latest else [p for p in deps.tree.dirty(root) if project.covers(p)]
        deps.trace.step("changes", count=len(changed), files=changed[:200], baseline=latest is None)
        restart_needed = needs_restart(project, changed)
        if latest is not None and not changed:
            ok, report = health(deps, project, wait=False)
            return result(ok, True, f"{project.name}: nothing new to deploy; release {latest.release_id} is live"
                          + ("" if ok else " but health checks fail"), release_id=latest.release_id, health=report)
        if req.dry_run:
            return result(True, False, f"dry run: would deploy {len(changed)} file(s) to {project.name}"
                          + (f" and restart {', '.join(project.units + project.self_units)}" if restart_needed else ""),
                          changed=changed[:200], restart=restart_needed, checks=[" ".join(c) for c in project.checks])
        ok, checks = run_checks(deps, project)
        if not ok:
            return result(False, True, f"{project.name}: checks failed, nothing was deployed", checks=checks,
                          changed=changed[:200])
        bumped = bust_cache(deps, project, changed) if latest else []   # a first run only records what is live
        release = record(deps, project, "deploy", req, latest.release_id if latest else None)
        restarted = restart(deps, project, project.units + project.self_units) if restart_needed else {}
        healthy, report = health(deps, project, wait=True)
        if healthy:
            return result(True, True, f"{project.name}: deployed {len(changed)} file(s) as release {release.release_id}"
                          + (f", restarted {', '.join(restarted)}" if restarted else ", no restart needed")
                          + "; health checks pass", release_id=release.release_id, changed=changed[:200],
                          cache_bust=bumped, restarted=restarted, checks=checks, health=report)
        return self._undo(deps, req, project, latest, release, report, changed)

    def _git_pull(self, deps: Deps, req: DeployRequest, project: Project) -> OperationResult:
        root = root_of(project)
        head = deps.tree.head(root)
        remote = deps.tree.fetch(root, project.remote, project.branch)
        deps.trace.step("fetch", head=head, remote=remote)
        latest = deps.releases.latest(project.name)
        if remote == head:
            if latest is None:
                latest = record(deps, project, "deploy", req, None)
            ok, report = health(deps, project, wait=False)
            return result(ok, True, f"{project.name}: already at {project.remote}/{project.branch} ({head[:10] if head else '?'})"
                          + ("" if ok else "; health checks fail"), release_id=latest.release_id, health=report)
        changed = deps.tree.changed(root, head or remote, remote)
        restart_needed = needs_restart(project, changed)
        if req.dry_run:
            return result(True, False, f"dry run: would move {project.name} from {(head or '?')[:10]} to {remote[:10]} "
                          f"({len(changed)} file(s))" + (", then restart its units" if restart_needed else ""),
                          changed=changed[:200], restart=restart_needed)
        if latest is None or latest.head != head:      # the live commit is what a failure goes back to
            latest = record(deps, project, "deploy", req.model_copy(update={"reason": "the state before " + deps.adw_id}),
                            latest.release_id if latest else None, head=head)
        deps.tree.fast_forward(root, remote)
        deps.trace.step("fast_forward", head=remote, files=len(changed))
        ok, checks = run_checks(deps, project)
        if not ok:
            deps.tree.reset_keep(root, head or remote)
            deps.trace.step("reset_keep", head=head, reason="checks failed")
            return result(False, True, f"{project.name}: checks failed on {remote[:10]}; went back to {(head or '?')[:10]}",
                          checks=checks, changed=changed[:200])
        release = record(deps, project, "deploy", req, latest.release_id, head=remote)
        restarted = restart(deps, project, project.units + project.self_units) if restart_needed else {}
        healthy, report = health(deps, project, wait=True)
        if healthy:
            later = [u for u, s in restarted.items() if s.startswith("scheduled")]
            return result(True, True, f"{project.name}: deployed {remote[:10]} ({len(changed)} file(s)) as release "
                          f"{release.release_id}" + (f", restarted {', '.join(restarted)}" if restarted else "")
                          + "; health checks pass" + (f" (ask for health again in a minute for {', '.join(later)})" if later else ""),
                          release_id=release.release_id, changed=changed[:200], restarted=restarted, checks=checks,
                          health=report)
        return self._undo(deps, req, project, latest, release, report, changed)

    @staticmethod
    def _undo(deps: Deps, req: DeployRequest, project: Project, previous: Release | None, failed: Release,
              report: list[dict], changed: list[str]) -> OperationResult:
        if previous is None:
            return result(False, True, f"{project.name}: deployed as {failed.release_id} but health checks fail, and "
                          "there is no earlier release to go back to", release_id=failed.release_id, health=report)
        restored = restore(deps, project, previous)
        back = record(deps, project, "rollback", req, failed.release_id)
        restart(deps, project, project.units + project.self_units)
        healthy, after = health(deps, project, wait=True)
        return result(False, True, f"{project.name}: health checks failed after the deploy, so release "
                      f"{previous.release_id} was put back ({len(restored)} file(s))"
                      + ("; it is healthy again" if healthy else "; it is STILL not healthy -- look at the logs"),
                      failed_release=failed.release_id, rolled_back_to=previous.release_id, rollback_release=back.release_id,
                      health_after_deploy=report, health_after_rollback=after, changed=changed[:200])


class Restart:
    name, writes, needs_project = "restart", True, True

    def run(self, deps: Deps, req: DeployRequest, project: Project) -> OperationResult:
        units = project.units + project.self_units
        if req.unit:
            if req.unit not in units:
                raise Refused(f"{project.name} restarts only: {', '.join(units) or 'nothing'}")
            units = [req.unit]
        if not units:
            raise Refused(f"{project.name} has no services to restart")
        if req.dry_run:
            return result(True, False, f"dry run: would restart {', '.join(units)}", units=units)
        with deps.lock.hold(project.name):
            done = restart(deps, project, units)
            healthy, report = health(deps, project, wait=True)
        return result(healthy, True, f"{project.name}: " + ", ".join(f"{u} {s}" for u, s in done.items())
                      + ("; health checks pass" if healthy else "; health checks FAIL"), restarted=done, health=report)


class Rollback:
    name, writes, needs_project = "rollback", True, True

    def run(self, deps: Deps, req: DeployRequest, project: Project) -> OperationResult:
        if project.source == "none":
            raise Refused(f"{project.name} has no releases here")
        with deps.lock.hold(project.name):
            latest = deps.releases.latest(project.name)
            if latest is None:
                raise Refused(f"{project.name} has no recorded release yet")
            target_id = req.release_id or latest.previous or ""
            target = deps.releases.get(project.name, target_id) if target_id else None
            if target is None:
                raise Refused(f"no release {target_id or '(before ' + latest.release_id + ')'} for {project.name}; "
                              "ask for history to see the releases")
            root = root_of(project)
            if project.source == "in_place":
                pending = diff(deps.snapshots.scan(project, root), target.files)
            else:
                pending = deps.tree.changed(root, target.head or "", deps.tree.head(root) or "") if target.head else []
            if req.dry_run:
                return result(True, False, f"dry run: would put {project.name} back to release {target.release_id} "
                              f"({len(pending)} file(s))", release_id=target.release_id, changed=pending[:200])
            if not req.reason.strip():
                raise Refused("say why it is being rolled back (reason), so the record shows it")
            saved = record(deps, project, "pre_rollback", req, latest.release_id)
            changed = restore(deps, project, target)
            back = record(deps, project, "rollback", req, latest.release_id, head=target.head)
            done = restart(deps, project, project.units + project.self_units) if changed else {}
            healthy, report = health(deps, project, wait=True)
        return result(healthy, True, f"{project.name}: rolled back to release {target.release_id} "
                      f"({len(changed)} file(s)); the replaced state is saved as {saved.release_id}"
                      + ("; health checks pass" if healthy else "; health checks FAIL"),
                      release_id=back.release_id, rolled_back_to=target.release_id, undo_with=saved.release_id,
                      changed=changed[:200], restarted=done, health=report)
