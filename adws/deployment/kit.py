"""The Deployment ADW's composition root: the only module that names concrete classes.

State lives outside every repository it deploys (a rollback of a repo must not roll back its own record):
`$ADW_DEPLOY_HOME`, default `~/.local/share/adw_deploy/` -- releases/, blobs/, runs/<adw_id>.json,
ledger.jsonl, locks/.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

from .adapters.check_runner import SubprocessChecks
from .adapters.clock import SystemClock
from .adapters.git_tree import GitSourceTree
from .adapters.health_probe import HealthProbe
from .adapters.journal_logs import JournalLogs
from .adapters.ledger import JsonlLedger
from .adapters.project_catalog import JsonProjectCatalog
from .adapters.project_lock import FlockProjectLock
from .adapters.release_store import FileReleaseStore
from .adapters.run_trace import RunTrace
from .adapters.snapshots import FileSnapshotter
from .adapters.systemd_services import SystemdUserServices
from .operations.common import Deps
from .operations.runner import DeploymentRunner

PROJECTS = Path(__file__).with_name("projects.json")


def deploy_home() -> Path:
    return Path(os.environ.get("ADW_DEPLOY_HOME") or Path.home() / ".local" / "share" / "adw_deploy")


def build_runner(projects: Path = PROJECTS, home: Path | None = None, adw_id: str | None = None) -> DeploymentRunner:
    home = home or deploy_home()
    clock = SystemClock()
    services = SystemdUserServices()
    deps = Deps(catalog=JsonProjectCatalog(projects), tree=GitSourceTree(), snapshots=FileSnapshotter(),
                releases=FileReleaseStore(home), services=services, probe=HealthProbe(services), logs=JournalLogs(),
                checks=SubprocessChecks(), ledger=JsonlLedger(home / "ledger.jsonl"), trace=RunTrace(clock),
                lock=FlockProjectLock(home / "locks"), clock=clock, adw_id=adw_id or uuid.uuid4().hex[:8])
    return DeploymentRunner(deps, home / "runs")
