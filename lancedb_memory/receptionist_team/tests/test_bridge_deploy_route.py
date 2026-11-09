"""Toyota's route to the Deployment ADW: form -> checked JSON prompt, the lane, the read-back, and the refusals.

Offline: no Bridge, no worker, no ADW, no model. The prompts are compared against the worker's own table
(agent_bridge_worker/deploy_agent.py: READS, WRITES, FLAGS), imported rather than copied, so a rename on
either side fails here instead of being discovered when a deploy is refused.

    cd lancedb_memory
    .venv/bin/python -m receptionist_team.tests.test_bridge_deploy_route
"""
import json
import re

from agent_bridge_worker.deploy_agent import FLAGS, READS, WRITES

from ..bridge_client.deploy_plans import DEPLOY_PLANS, MAX_LINES, MAX_REASON
from ..bridge_client.models import BridgeAgent
from ..bridge_client.specialists import SPECIALISTS, FormProblem, SpecialistForm

DEPLOY = BridgeAgent(name="deploy", operations=["inspect", "implement"],
                     workspaces=["deploy"], write_workspaces=["deploy"])
PASSED = []


def check(name, condition, detail=""):
    assert condition, f"{name}: {detail}"
    PASSED.append(name)


def build(**form):
    return SPECIALISTS["bridge_deploy"].builder.build(SpecialistForm.model_validate(form), DEPLOY)


def refused(**form):
    try:
        build(**form)
    except FormProblem as problem:
        return problem.spoken
    raise AssertionError(f"{form} was accepted and should not have been")


def asked(request):
    return json.loads(request.prompt)


# -- wired to the deploy agent only, with the worker's operations ---------------
spec = SPECIALISTS["bridge_deploy"]
check("specialist sends only to the Bridge's deploy agent", spec.bridge_agent == "deploy", spec.bridge_agent)
check("the plans are exactly the worker's operations", set(DEPLOY_PLANS) == set(READS) | set(WRITES),
      set(DEPLOY_PLANS) ^ (set(READS) | set(WRITES)))
for name, plan in DEPLOY_PLANS.items():
    lane = "inspect" if name in READS else "implement"
    check(f"{name} is sent in the worker's lane", plan.operation == lane, plan.operation)

# -- reads need no yes; changes are read back and wait for one -----------------
for name in sorted(READS):
    req, _ = build(operation=name, project="dashboard")
    check(f"{name} is a read", (req.operation, req.approval_mode) == ("inspect", "read_only"), req.operation)
    check(f"{name} needs no yes", not req.needs_confirmation)
    check(f"{name} goes to the deploy workspace", req.workspace == "deploy", req.workspace)
for name in sorted(WRITES):
    req, said = build(operation=name, project="dashboard", reason="ship the blink")
    check(f"{name} is a change", (req.operation, req.approval_mode) == ("implement", "workspace_write"))
    check(f"{name} is read back and waits for a yes", req.needs_confirmation and said.strip(), said)

# -- every field sent is one the worker accepts, and matches its pattern --------
for name in DEPLOY_PLANS:
    req, _ = build(operation=name, project="dashboard", reason="why", release_id="r-20261108-abc",
                   unit="dashboard-server.service", lines=40, dry_run=True)
    body = asked(req)
    unknown = set(body) - set(FLAGS) - {"deploy", "dry_run"}
    check(f"{name} sends no field the worker would refuse", not unknown, unknown)
    check(f"{name} names its operation", body["deploy"] == name, body)
    for key, (_, pattern) in FLAGS.items():
        if key in body:
            check(f"{name}'s {key} passes the worker's pattern", re.fullmatch(pattern, str(body[key])), body[key])

# -- exact prompts --------------------------------------------------------------
check("projects needs nothing", asked(build(operation="projects")[0]) == {"deploy": "projects"})
req, said = build(operation="deploy", project="Dashboard", reason="  Notes:   the active warning blinks ")
check("deploy carries project and a tidied reason",
      asked(req) == {"deploy": "deploy", "project": "dashboard", "reason": "Notes: the active warning blinks"},
      asked(req))
check("the deploy read-back promises checks and the automatic undo",
      "checks run first" in said and "goes back by itself" in said, said)
req, said = build(operation="deploy", project="dashboard", dry_run=True)
check("a dry run needs no reason", asked(req) == {"deploy": "deploy", "project": "dashboard", "dry_run": True},
      asked(req))
check("a dry run says nothing changes", "without changing anything" in said, said)
req, said = build(operation="rollback", project="dashboard", reason="broke", release_id="r1")
check("rollback carries the release", asked(req)["release_id"] == "r1", asked(req))
check("the rollback read-back says it can be undone", "can itself be undone" in said, said)
_, said = build(operation="rollback", project="dashboard", reason="broke")
check("no release means the one before", "the release before this one" in said, said)
req, said = build(operation="restart", project="agent_blocks", unit="toyota-receptionist.service")
check("restart may name one service", asked(req)["unit"] == "toyota-receptionist.service", asked(req))
req, _ = build(operation="logs", project="dashboard", lines=50)
check("logs carries the line count as a number", asked(req)["lines"] == 50, asked(req))

# -- refusals say what to do instead -------------------------------------------
check("a deploy without a reason is refused", "reason" in refused(operation="deploy", project="dashboard"))
check("a rollback without a reason is refused", "reason" in refused(operation="rollback", project="dashboard"))
check("a missing project is refused", "Which project" in refused(operation="health"))
check("a sentence for a project is refused", "list the projects" in refused(operation="health", project="the dash board"))
check("a path for a project is refused", "list the projects" in refused(operation="deploy", project="/etc", reason="x"))
check("a long reason is refused",
      str(MAX_REASON) in refused(operation="deploy", project="dashboard", reason="z" * (MAX_REASON + 1)))
check("too many log lines is refused", f"1 to {MAX_LINES}" in refused(operation="logs", project="dashboard", lines=5000))
check("a made-up release is refused", "history" in refused(operation="rollback", project="dashboard",
                                                            reason="x", release_id="last tuesday's"))
check("a made-up service is refused", "services" in refused(operation="restart", project="dashboard", unit="rm -rf /"))
check("a missing operation is not guessed at", "What should I do" in refused(instructions="ship it"))
for spelling in ("shell", "exec", "delete", "push"):
    check(f"{spelling} is not an operation", "What should I do" in refused(operation=spelling, project="dashboard"))
for spelling in ("deploy", "deploy.deploy", "DEPLOY"):
    req, _ = build(operation=spelling, project="dashboard", reason="x")
    check(f"{spelling} reaches the deploy plan", asked(req)["deploy"] == "deploy", asked(req))

# -- the route is on Toyota's roster --------------------------------------------
from ..roles import ROLES, ROLE_BLURBS, ROLE_SPOKEN_NAMES, ROLE_START_TASKS, TOYOTA_DESK_ROLE_NAMES  # noqa: E402
from ..extra_roles import EXTRA_ROLES  # noqa: E402

check("the route is a defined role", "bridge_deploy" in ROLES and "bridge_deploy" in EXTRA_ROLES)
check("the route has a spoken name and a start task",
      "bridge_deploy" in ROLE_SPOKEN_NAMES and "bridge_deploy" in ROLE_START_TASKS)
check("the route is on Toyota's desk", "bridge_deploy" in TOYOTA_DESK_ROLE_NAMES)
check("the blurb promises the automatic undo", "put back by itself" in ROLE_BLURBS["bridge_deploy"])

if __name__ == "__main__":
    print(f"{len(PASSED)} passed")
