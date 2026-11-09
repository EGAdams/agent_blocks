"""One Plan per Deployment ADW operation: the model's filled form -> the exact JSON prompt, and the sentence read back.

Strategy + Registry, the same shape as `relay_plans.py` and `browser_plans.py`. `DeployBuilder`
(specialists.py) finds the Plan for `form.operation` in `DEPLOY_PLANS` and asks it to `build()`; adding an
operation is one class here and one line in `DEPLOY_PLANS`. The Plans mirror the worker's table
(`agent_bridge_worker/deploy_agent.py`: READS, WRITES, FLAGS), which re-checks everything, and the ADW
(`adws/adw_deploy.py`) re-checks it again against its fixed project table -- a request never names a path,
a command or a unit that table does not already hold.

What code decides, never the model:

- the operation's lane (`inspect` or `implement`), so a deploy can never be sent as a read;
- a deploy or a rollback carries a reason (the release record keeps it), unless it is a dry run;
- every change is read back and waits for a yes, like every other Bridge change Toyota makes.
"""
import re
from typing import Protocol

# The worker's own patterns (deploy_agent.FLAGS), so a value refused there is refused here first, in words.
PROJECT = re.compile(r"[a-z][a-z0-9_-]{0,40}")
RELEASE = re.compile(r"[A-Za-z0-9_-]{1,80}")
UNIT = re.compile(r"[A-Za-z0-9@._-]{1,80}")
MAX_REASON = 300
MAX_LINES = 400


class IDeployPlan(Protocol):
    name: str
    operation: str      # the Bridge lane: "inspect" or "implement"

    def build(self, form, problem) -> tuple[dict, str]:
        """(prompt dict, read-back sentence). `problem(spoken)` raises the specialist's FormProblem."""
        ...


def _project(form, problem) -> str:
    name = str(form.project or "").strip().lower()
    if not name:
        problem("Which project? Ask me to list the projects if you're not sure of the name.")
    if not PROJECT.fullmatch(name):
        problem("That isn't one of the project names. Ask me to list the projects.")
    return name


def _reason(form, problem, required: bool) -> dict:
    text = " ".join(str(form.reason or "").split())
    if not text:
        if required:
            problem("What's the reason? It goes in the release record, so later you can see why it went out.")
        return {}
    if len(text) > MAX_REASON:
        problem(f"Keep the reason under {MAX_REASON} characters.")
    return {"reason": text}


def _dry(form) -> dict:
    return {"dry_run": True} if form.dry_run else {}


class Projects:
    name, operation = "projects", "inspect"

    def build(self, form, problem):
        return {"deploy": "projects"}, ""


class _ProjectRead:
    """status / health / history: one project, nothing changed."""

    operation = "inspect"

    def __init__(self, name: str) -> None:
        self.name = name

    def build(self, form, problem):
        return {"deploy": self.name, "project": _project(form, problem)}, ""


class Logs:
    name, operation = "logs", "inspect"

    def build(self, form, problem):
        req: dict = {"deploy": "logs", "project": _project(form, problem)}
        unit = str(form.unit or "").strip()
        if unit:
            if not UNIT.fullmatch(unit):
                problem("That doesn't look like one of the project's services. Leave it out for the main one.")
            req["unit"] = unit
        if form.lines is not None:
            lines = int(form.lines)
            if not 1 <= lines <= MAX_LINES:
                problem(f"I can read 1 to {MAX_LINES} lines.")
            req["lines"] = lines
        return req, ""


class Deploy:
    """Check, record, make live, health-check -- and put the last release back by itself if health fails."""

    name, operation = "deploy", "implement"

    def build(self, form, problem):
        project = _project(form, problem)
        req = {"deploy": "deploy", "project": project, **_reason(form, problem, not form.dry_run), **_dry(form)}
        if form.dry_run:
            return req, f"I'll ask what a deploy of {project} would change, without changing anything."
        return req, (f"I'll deploy {project}: its checks run first, then it goes live and gets a health check. "
                     "If the health check fails, the last release goes back by itself.")


class Restart:
    name, operation = "restart", "implement"

    def build(self, form, problem):
        project = _project(form, problem)
        req: dict = {"deploy": "restart", "project": project}
        unit = str(form.unit or "").strip()
        if unit:
            if not UNIT.fullmatch(unit):
                problem("That doesn't look like one of the project's services. Leave it out to restart them all.")
            req["unit"] = unit
        what = unit or f"{project}'s services"
        return req, f"I'll restart {what} and then check that they came back."


class Rollback:
    name, operation = "rollback", "implement"

    def build(self, form, problem):
        project = _project(form, problem)
        req: dict = {"deploy": "rollback", "project": project}
        release = str(form.release_id or "").strip()
        if release:
            if not RELEASE.fullmatch(release):
                problem("That isn't a release id. Ask me for the project's history to see them.")
            req["release_id"] = release
        req.update(_reason(form, problem, not form.dry_run))
        req.update(_dry(form))
        target = f"release {release}" if release else "the release before this one"
        if form.dry_run:
            return req, f"I'll ask what rolling {project} back to {target} would change, without changing anything."
        return req, (f"I'll roll {project} back to {target}. What's live now is saved first, "
                     "so the rollback can itself be undone.")


DEPLOY_PLANS: dict[str, IDeployPlan] = {p.name: p for p in (
    Projects(), _ProjectRead("status"), _ProjectRead("health"), Logs(), _ProjectRead("history"),
    Deploy(), Restart(), Rollback(),
)}
