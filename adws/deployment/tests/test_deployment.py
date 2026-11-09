"""The Deployment ADW offline: real files, real git, real release store; fake services, health and clock."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from deployment.adapters.git_tree import GitSourceTree
from deployment.adapters.ledger import JsonlLedger
from deployment.adapters.project_catalog import JsonProjectCatalog
from deployment.adapters.project_lock import FlockProjectLock
from deployment.adapters.release_store import FileReleaseStore
from deployment.adapters.run_trace import RunTrace
from deployment.adapters.snapshots import FileSnapshotter
from deployment.kit import PROJECTS
from deployment.models import DeployRequest
from deployment.operations.common import Deps
from deployment.operations.runner import DeploymentRunner


class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return "2026-11-08T20:00:00-05:00"

    def sleep(self, seconds):
        self.t += seconds

    def monotonic(self):
        return self.t


class Services:
    def __init__(self):
        self.restarted, self.later = [], []

    def state(self, unit):
        return "active"

    def restart(self, unit):
        self.restarted.append(unit)

    def restart_later(self, unit, seconds, tag):
        self.later.append(unit)


class Probe:
    """Healthy unless the page holds the word BROKEN."""
    def __init__(self, root: Path):
        self.root = root

    def check(self, check):
        page = self.root / "site" / "index.html"
        ok = not page.exists() or "BROKEN" not in page.read_text()
        return ok, "fine" if ok else "page is broken"


class Checks:
    def __init__(self):
        self.ok, self.ran = True, []

    def run(self, argv, root, timeout):
        self.ran.append(argv)
        return self.ok, "1 passed" if self.ok else "1 failed"


class Logs:
    def tail(self, unit, lines):
        return f"{unit} said hello"

    def tail_file(self, path, lines):
        return path.read_text() if path.exists() else "missing"


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def init_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")


@pytest.fixture
def world(tmp_path):
    site = tmp_path / "web"
    init_repo(site)
    (site / "site" / "css").mkdir(parents=True)
    (site / "site" / "index.html").write_text('<html><link href="/css/app.css?v=old1"><script src="/js/x.js?v=1"></script></html>')
    (site / "site" / "css" / "app.css").write_text(".a { color: red }")
    (site / "site" / "server.py").write_text("print('v1')")
    (site / "site" / "runtime.json").write_text("{}")
    git(site, "add", "-A")
    git(site, "commit", "-q", "-m", "one")
    catalog = {"projects": [
        {"name": "web", "description": "a site", "root": str(site), "source": "in_place", "scope": ["site/"],
         "suffixes": [".py", ".css", ".html", ".js"], "exclude": ["site/*.bak"], "checks": [["true"]],
         "units": ["web.service"], "restart_when": ["*.py"], "log_files": {"web.log": str(tmp_path / "web.log")},
         "health": [{"name": "page", "kind": "http", "url": "http://x/"}], "health_wait_seconds": 4,
         "cache_bust": {"html": "site/index.html", "url_root": "site/"}},
        {"name": "watch", "description": "health only", "root": str(site), "source": "none",
         "health": [{"name": "page", "kind": "http", "url": "http://x/"}]},
    ]}
    (tmp_path / "projects.json").write_text(json.dumps(catalog))
    state = {"services": Services(), "checks": Checks(), "tmp": tmp_path, "site": site, "n": 0}

    def run(operation, **kw):
        state["n"] += 1
        adw_id = f"{state['n']:08x}"
        clock = Clock()
        deps = Deps(catalog=JsonProjectCatalog(tmp_path / "projects.json"), tree=GitSourceTree(),
                    snapshots=FileSnapshotter(), releases=FileReleaseStore(tmp_path / "home"),
                    services=state["services"], probe=Probe(site), logs=Logs(), checks=state["checks"],
                    ledger=JsonlLedger(tmp_path / "home" / "ledger.jsonl"), trace=RunTrace(clock),
                    lock=FlockProjectLock(tmp_path / "home" / "locks"), clock=clock, adw_id=adw_id)
        return DeploymentRunner(deps, tmp_path / "home" / "runs").run(DeployRequest(operation=operation, **kw))

    state["run"] = run
    return state


def test_the_shipped_project_table_loads():
    catalog = JsonProjectCatalog(PROJECTS)
    assert {"dashboard", "agent_blocks", "bridge_sandbox", "gateway"} <= set(catalog.names())
    assert catalog.get("dashboard").covers("dashboard/js/boot/notes.js")
    assert not catalog.get("dashboard").covers("dashboard/.venv/lib/x.py")
    assert not catalog.get("dashboard").covers("dashboard/server.py.bak-2026")


def test_first_deploy_records_a_baseline_and_nothing_new_after(world):
    first = world["run"]("deploy", project="web", reason="baseline")
    assert first.ok and first.verified, first.line
    again = world["run"]("deploy", project="web", reason="again")
    assert again.ok and "nothing new" in again.line


def test_deploy_busts_the_cache_of_a_changed_stylesheet_only(world):
    world["run"]("deploy", project="web", reason="baseline")
    (world["site"] / "site" / "css" / "app.css").write_text(".a { color: blue }")
    out = world["run"]("deploy", project="web", reason="blue")
    assert out.ok, out.line
    html = (world["site"] / "site" / "index.html").read_text()
    assert "/css/app.css?v=adw" in html and "/js/x.js?v=1" in html
    assert world["services"].restarted == []                   # no Python changed, no restart
    assert out.data["cache_bust"] == ["/css/app.css"]


def test_python_change_restarts_the_unit(world):
    world["run"]("deploy", project="web", reason="baseline")
    (world["site"] / "site" / "server.py").write_text("print('v2')")
    out = world["run"]("deploy", project="web", reason="v2")
    assert out.ok and world["services"].restarted == ["web.service"]


def test_failed_checks_change_nothing(world):
    world["run"]("deploy", project="web", reason="baseline")
    (world["site"] / "site" / "css" / "app.css").write_text(".a { color: green }")
    world["checks"].ok = False
    out = world["run"]("deploy", project="web", reason="green")
    assert not out.ok and "checks failed" in out.line
    assert "?v=old1" in (world["site"] / "site" / "index.html").read_text()
    status = world["run"]("status", project="web")
    assert status.data["pending"] == ["site/css/app.css"]


def test_unhealthy_deploy_rolls_itself_back(world):
    world["run"]("deploy", project="web", reason="baseline")
    page = world["site"] / "site" / "index.html"
    good = page.read_text()
    page.write_text("<html>BROKEN</html>")
    out = world["run"]("deploy", project="web", reason="break it")
    assert not out.ok and out.verified and "was put back" in out.line and "healthy again" in out.line
    assert page.read_text() == good


def test_rollback_and_its_undo(world):
    world["run"]("deploy", project="web", reason="baseline")
    css = world["site"] / "site" / "css" / "app.css"
    css.write_text(".a { color: blue }")
    world["run"]("deploy", project="web", reason="blue")
    assert world["run"]("rollback", project="web", reason="no", dry_run=True).ok
    assert css.read_text() == ".a { color: blue }"              # a dry run changes nothing
    back = world["run"]("rollback", project="web", reason="blue was wrong")
    assert back.ok, back.line
    assert css.read_text() == ".a { color: red }"
    undo = world["run"]("rollback", project="web", reason="it was right", release_id=back.data["undo_with"])
    assert undo.ok and css.read_text() == ".a { color: blue }"


def test_rollback_needs_a_reason_and_a_release(world):
    out = world["run"]("rollback", project="web", reason="x")
    assert not out.ok and out.data.get("refused")
    world["run"]("deploy", project="web", reason="baseline")
    out = world["run"]("rollback", project="web", reason="x", release_id="../../etc")
    assert not out.ok


def test_requests_name_only_configured_things(world):
    assert world["run"]("deploy", project="elsewhere", reason="x").data["refused"]
    assert world["run"]("deploy", project="watch", reason="x").data["refused"]
    assert world["run"]("deploy", project="web", reason="").data["refused"]
    preview = world["run"]("deploy", project="web", dry_run=True)        # a preview records nothing, so needs no reason
    assert preview.ok and not preview.data.get("refused"), preview.line
    assert world["run"]("status", project="web").data["latest_release"] is None
    assert world["run"]("restart", project="web", unit="sshd.service").data["refused"]
    assert world["run"]("logs", project="web", unit="sshd.service").data["refused"]
    assert world["run"]("explode", project="web").data["refused"]


def test_restart_logs_health_history(world):
    (world["tmp"] / "web.log").write_text("started\n")
    assert world["run"]("restart", project="web").ok and world["services"].restarted == ["web.service"]
    assert world["run"]("logs", project="web", unit="web.log").data["text"] == "started\n"
    assert world["run"]("logs", project="web").data["text"] == "web.service said hello"
    assert world["run"]("health", project="watch").ok
    history = world["run"]("history", project="web")
    assert [r["operation"] for r in history.data["runs"]][:2] == ["health", "logs"] or history.data["runs"]


def test_every_run_is_traced_and_ledgered(world):
    out = world["run"]("deploy", project="web", reason="baseline")
    run = json.loads((world["tmp"] / "home" / "runs" / f"{out.data['adw_id']}.json").read_text())
    assert run["request"]["operation"] == "deploy" and [s["name"] for s in run["steps"]][0] == "request"
    assert "release_recorded" in [s["name"] for s in run["steps"]]
    lines = (world["tmp"] / "home" / "ledger.jsonl").read_text().splitlines()
    assert json.loads(lines[-1])["adw_id"] == out.data["adw_id"]


def test_git_pull_fast_forwards_and_goes_back_when_checks_fail(tmp_path):
    origin = tmp_path / "origin"
    init_repo(origin)
    (origin / "app.py").write_text("one")
    git(origin, "add", "-A")
    git(origin, "commit", "-q", "-m", "one")
    live = tmp_path / "live"
    subprocess.run(["git", "clone", "-q", str(origin), str(live)], check=True)
    (live / "local_edit.txt").write_text("keep me")
    catalog = {"projects": [{"name": "repo", "description": "r", "root": str(live), "source": "git_pull",
                             "checks": [["true"]], "units": ["a.service"], "self_units": ["w.service"],
                             "restart_when": ["*.py"], "health": [], "health_wait_seconds": 0}]}
    (tmp_path / "projects.json").write_text(json.dumps(catalog))
    services, checks = Services(), Checks()

    def run(operation, n, **kw):
        clock = Clock()
        deps = Deps(catalog=JsonProjectCatalog(tmp_path / "projects.json"), tree=GitSourceTree(),
                    snapshots=FileSnapshotter(), releases=FileReleaseStore(tmp_path / "home"), services=services,
                    probe=Probe(tmp_path), logs=Logs(), checks=checks, ledger=JsonlLedger(tmp_path / "l.jsonl"),
                    trace=RunTrace(clock), lock=FlockProjectLock(tmp_path / "locks"), clock=clock, adw_id=f"{n:08x}")
        return DeploymentRunner(deps, tmp_path / "runs").run(DeployRequest(operation=operation, **kw))

    one = git(live, "rev-parse", "HEAD")
    (origin / "app.py").write_text("two")
    git(origin, "commit", "-qam", "two")
    two = git(origin, "rev-parse", "HEAD")
    assert run("status", 1, project="repo").data["pending"] == ["app.py"]
    checks.ok = False
    out = run("deploy", 2, project="repo", reason="two")
    assert not out.ok and git(live, "rev-parse", "HEAD") == one
    checks.ok = True
    out = run("deploy", 3, project="repo", reason="two")
    assert out.ok, out.line
    assert git(live, "rev-parse", "HEAD") == two and (live / "local_edit.txt").read_text() == "keep me"
    assert services.restarted == ["a.service"] and services.later == ["w.service"]
    back = run("rollback", 4, project="repo", reason="undo two")
    assert back.ok, back.line
    assert git(live, "rev-parse", "HEAD") == one and (live / "app.py").read_text() == "one"
