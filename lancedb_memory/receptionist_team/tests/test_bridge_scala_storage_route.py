"""Toyota's route to Scala's filing: form -> checked JSON prompt, the lane, the read-back, and the refusals.

Offline: no Bridge, no worker, no ADW, no model. The prompts are compared against the worker's own table
(agent_bridge_worker/scala_storage_agent.py: READS, WRITES, FLAGS, NEEDS), imported rather than copied, so a
rename on either side fails here instead of being discovered when a store is refused.

    cd lancedb_memory
    .venv/bin/python -m receptionist_team.tests.test_bridge_scala_storage_route
"""
import json
import re

from agent_bridge_worker.scala_storage_agent import FLAGS, NEEDS, READS, WRITES

from ..bridge_client.models import BridgeAgent
from ..bridge_client.specialists import SPECIALISTS, FormProblem, SpecialistForm
from ..bridge_client.storage_plans import STORAGE_PLANS

STORAGE = BridgeAgent(name="scala-storage", operations=["inspect", "implement"],
                      workspaces=["scala-storage"], write_workspaces=["scala-storage"])
SCAN = "scan_01M787R1STEA7N30DM8PG13MSZ"
FULL = {"scan_id": SCAN, "machine": "BATI", "folder": "insurance/home"}
PASSED = []


def check(name, condition, detail=""):
    assert condition, f"{name}: {detail}"
    PASSED.append(name)


def build(**form):
    return SPECIALISTS["bridge_scala_storage"].builder.build(SpecialistForm.model_validate(form), STORAGE)


def refused(**form):
    try:
        build(**form)
    except FormProblem as problem:
        return problem.spoken
    raise AssertionError(f"{form} was accepted and should not have been")


def asked(request):
    return json.loads(request.prompt)


# -- wired to the scala-storage agent only, with the worker's operations --------
spec = SPECIALISTS["bridge_scala_storage"]
check("specialist sends only to the Bridge's scala-storage agent", spec.bridge_agent == "scala-storage", spec.bridge_agent)
check("the plans are exactly the worker's operations", set(STORAGE_PLANS) == set(READS) | set(WRITES),
      set(STORAGE_PLANS) ^ (set(READS) | set(WRITES)))
for name, plan in STORAGE_PLANS.items():
    lane = "inspect" if name in READS else "implement"
    check(f"{name} is sent in the worker's lane", plan.operation == lane, plan.operation)

# -- reads need no yes; a store is read back and waits for one -----------------
for name in sorted(READS):
    req, _ = build(operation=name, **FULL)
    check(f"{name} is a read", (req.operation, req.approval_mode) == ("inspect", "read_only"), req.operation)
    check(f"{name} needs no yes", not req.needs_confirmation)
    check(f"{name} goes to the scala-storage workspace", req.workspace == "scala-storage", req.workspace)
for name in sorted(WRITES):
    req, said = build(operation=name, **FULL)
    check(f"{name} is a change", (req.operation, req.approval_mode) == ("implement", "workspace_write"))
    check(f"{name} is read back and waits for a yes", req.needs_confirmation and said.strip(), said)

# -- every field sent is one the worker accepts, and carries what it needs -----
for name in STORAGE_PLANS:
    body = asked(build(operation=name, **FULL)[0])
    unknown = set(body) - set(FLAGS) - {"storage"}
    check(f"{name} sends no field the worker would refuse", not unknown, unknown)
    check(f"{name} names its operation", body["storage"] == name, body)
    check(f"{name} carries every field the worker needs", set(NEEDS[name]) <= set(body), body)
    for key, (_, pattern) in FLAGS.items():
        if key in body:
            check(f"{name}'s {key} passes the worker's pattern", re.fullmatch(pattern, body[key]), body[key])

# -- exact prompts --------------------------------------------------------------
check("targets needs nothing", asked(build(operation="targets")[0]) == {"storage": "targets"})
check("status needs nothing", asked(build(operation="status")[0]) == {"storage": "status"})
check("folders carries only the machine",
      asked(build(operation="folders", machine="Mom's", folder="x", scan_id=SCAN)[0])
      == {"storage": "folders", "machine": "Mom's"})
req, said = build(operation="store", **FULL)
check("the example store is exact", asked(req) == {"storage": "store", **FULL}, asked(req))
check("the read-back names the scan, machine and folder", SCAN in said and "BATI" in said and "insurance/home" in said, said)
check("the read-back says it is a copy and the original stays", "copy" in said and "original stays" in said, said)
req, said = build(operation="store", scan_id="LAST", machine="R46", folder="bills/electric")
check("the last scan may be named", asked(req)["scan_id"] == "last", asked(req))
check("the last scan is read back in words", "the last scan" in said, said)
req, _ = build(operation="verify", scan_id=SCAN.lower(), machine=" bati ", folder="insurance/home")
check("a lower-case scan id is put right", asked(req)["scan_id"] == SCAN, asked(req))
check("a machine is trimmed", asked(req)["machine"] == "bati", asked(req))
req, _ = build(operation="retrieve", **FULL)
check("retrieve carries no folder", asked(req) == {"storage": "retrieve", "scan_id": SCAN, "machine": "BATI"}, asked(req))
for spelling in ("store", "storage.store", "STORE"):
    check(f"{spelling} reaches the store plan", asked(build(operation=spelling, **FULL)[0])["storage"] == "store")

# -- refusals say what to do instead -------------------------------------------
for word in ("move", "delete", "remove", "overwrite"):
    check(f"{word} is answered with 'only copies'", "only copies" in refused(operation=word, **FULL))
check("a missing operation is not guessed at", "What should I do" in refused(**FULL))
for spelling in ("shell", "exec", "copy_anywhere"):
    check(f"{spelling} is not an operation", "What should I do" in refused(operation=spelling, **FULL))
check("a store without a scan is refused", "Which scan" in refused(operation="store", machine="BATI", folder="insurance/home"))
check("a store without a machine is refused", "Which machine" in refused(operation="store", scan_id=SCAN, folder="insurance/home"))
check("a store without a folder is refused", "Which folder" in refused(operation="store", scan_id=SCAN, machine="BATI"))
check("health needs a machine", "Which machine" in refused(operation="health"))
check("a made-up scan id is refused", "isn't a scan id" in refused(operation="store", scan_id="the mortgage one",
                                                                  machine="BATI", folder="insurance/home"))
for bad in ("../etc", "insurance/../../root", "~/secrets", "C:\\Users\\rbarn", "insurance;rm -rf /", "a" * 201):
    check(f"folder {bad[:20]!r} is refused", "approved folders" in refused(operation="store", scan_id=SCAN,
                                                                         machine="BATI", folder=bad))
check("a machine that is a command is refused", "isn't one of the machines" in refused(operation="health", machine="$(reboot)"))

# -- the route is on Toyota's roster --------------------------------------------
from ..roles import ROLES, ROLE_BLURBS, ROLE_SPOKEN_NAMES, ROLE_START_TASKS, TOYOTA_DESK_ROLE_NAMES  # noqa: E402
from ..extra_roles import EXTRA_ROLES  # noqa: E402

check("the route is a defined role", "bridge_scala_storage" in ROLES and "bridge_scala_storage" in EXTRA_ROLES)
check("the route has a spoken name and a start task",
      "bridge_scala_storage" in ROLE_SPOKEN_NAMES and "bridge_scala_storage" in ROLE_START_TASKS)
check("the route is on Toyota's desk", "bridge_scala_storage" in TOYOTA_DESK_ROLE_NAMES)
check("the blurb promises a copy", "always a copy" in ROLE_BLURBS["bridge_scala_storage"])

# -- a filing request reaches this route even when it names Scala -------------
from ..adapters.scala_route import ScalaAnswerRoute  # noqa: E402
from ..adapters.scala_storage_route import ScalaStorageRoute  # noqa: E402
from ..profiles import TOYOTA  # noqa: E402


def first_opinion(message):
    return TOYOTA.route_override.forced_agent(f"USER: {message}\nAGENT:")


for message in (f"Scala, copy {SCAN} to BATI under insurance/home", "file the last scan in Mom's cabinet",
                "what folders are on R46", "is BATI up", f"did {SCAN} get filed", "check the copy on SQMC",
                "put that scan on Rosemary46 in bills/electric"):
    check(f"{message!r} goes to Scala's filing", ScalaStorageRoute().forced_agent(f"USER: {message}\nAGENT:")
          == "bridge_scala_storage")
    check(f"{message!r} wins Toyota's route panel", first_opinion(message) == "bridge_scala_storage",
          first_opinion(message))
for message in ("Scala, file #7 under documents_511/insurance", "put to-do 7 in bills_511/water",
                "what is scala waiting on?"):
    check(f"{message!r} stays with Scala's questions", first_opinion(message) == "scala_questions",
          first_opinion(message))
for message in ("check SQMC's dashboard logs", "how much did we spend in 2025", "hello Toyota",
                "deploy agent_blocks on SQMC", "print the test page"):
    check(f"{message!r} is not a filing", ScalaStorageRoute().forced_agent(f"USER: {message}\nAGENT:") is None)
check("only the latest message counts",
      ScalaStorageRoute().forced_agent(f"USER: copy {SCAN} to BATI\nAGENT: ok\nUSER: thanks\nAGENT:") is None)
check("Scala's answer route would have taken it", ScalaAnswerRoute().forced_agent(f"USER: Scala, copy {SCAN} to BATI\nAGENT:"))

if __name__ == "__main__":
    print(f"{len(PASSED)} passed")
