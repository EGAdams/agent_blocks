"""The receptionists' route to Cashy on R46: form -> checked JSON prompt, the lane, the read-back, the refusals,
and the forced route that keeps "Cashy on R46" off the dashboard's own Cashy window.

Offline: no Bridge, no worker, no Cashy, no model. The prompts are compared against the worker's own table
(agent_bridge_worker/cashy_agent.py: READS, WRITES, SESSION, BANK, FIELDS), imported rather than copied.

    cd lancedb_memory
    .venv/bin/python -m receptionist_team.tests.test_bridge_cashy_route
"""
import json

from agent_bridge_worker.cashy_agent import BANK, FIELDS, READS, SESSION, WRITES

from ..bridge_client.cashy_plans import CASHY_PLANS
from ..bridge_client.models import BridgeAgent
from ..bridge_client.specialists import SPECIALISTS, FormProblem, SpecialistForm

CASHY = BridgeAgent(name="cashy-r46", operations=["inspect", "implement"],
                    workspaces=["cashy-r46"], write_workspaces=["cashy-r46"])
PASSED = []


def check(name, condition, detail=""):
    assert condition, f"{name}: {detail}"
    PASSED.append(name)


def build(**form):
    return SPECIALISTS["bridge_cashy"].builder.build(SpecialistForm.model_validate(form), CASHY)


def refused(**form):
    try:
        build(**form)
    except FormProblem as problem:
        return problem.spoken
    raise AssertionError(f"{form} was accepted and should not have been")


def asked(request):
    return json.loads(request.prompt)


spec = SPECIALISTS["bridge_cashy"]
check("specialist sends only to cashy-r46", spec.bridge_agent == "cashy-r46", spec.bridge_agent)
check("the plans are exactly the worker's operations", set(CASHY_PLANS) == set(READS) | set(WRITES),
      set(CASHY_PLANS) ^ (set(READS) | set(WRITES)))

for name in sorted(READS):
    req, said = build(operation=name)
    check(f"{name} is a read", (req.operation, req.approval_mode) == ("inspect", "read_only"), req.operation)
    check(f"{name} needs no yes", not req.needs_confirmation)
    check(f"{name} goes to the cashy-r46 workspace", req.workspace == "cashy-r46", req.workspace)
for name in sorted(WRITES):
    req, said = build(operation=name, bank="fifth_third")
    check(f"{name} is a change", (req.operation, req.approval_mode) == ("implement", "workspace_write"))
    check(f"{name} is read back and waits for a yes", req.needs_confirmation and said.strip(), said)

for name in CASHY_PLANS:
    body = asked(build(operation=name, bank="fnbo")[0])
    check(f"{name} sends only fields the worker knows", set(body) <= FIELDS, set(body) - FIELDS)
    check(f"{name} names machine R46 every time", body["machine"] == "R46", body)
    check(f"{name} names a session the worker accepts", SESSION.fullmatch(body["session"]), body)
    if "bank" in body:
        check(f"{name}'s bank passes the worker's pattern", BANK.fullmatch(body["bank"]), body)

check("status is exact", asked(build(operation="status")[0])
      == {"cashy": "status", "machine": "R46", "session": "r46-bridge"})
check("FNBO's status uses FNBO's session", asked(build(operation="status", bank="FNBO")[0])["session"] == "r46-fnbo")
req, said = build(operation="open_bank", bank="Fifth Third")
check("open_bank is exact", asked(req) == {"cashy": "open_bank", "machine": "R46", "session": "r46-bridge",
                                           "bank": "fifth_third"}, asked(req))
check("open_bank's read-back says no password is typed", "never types a password" in said, said)
req, said = build(operation="open_bank", bank="first national bank of omaha")
check("FNBO opens in its own session", asked(req)["session"] == "r46-fnbo" and asked(req)["bank"] == "fnbo", asked(req))
req, said = build(operation="logged_in")
check("logged_in is read back as signed in by EG himself", "signed in there yourself" in said, said)
check("a named session is kept", asked(build(operation="pause", session="r46-bridge")[0])["session"] == "r46-bridge")
check("cashy.status reaches status", asked(build(operation="cashy.status")[0])["cashy"] == "status")

for word in ("pay", "transfer", "approve", "password", "withdraw"):
    check(f"{word} is answered in words", "never moves money" in refused(operation=word))
check("a missing operation is not guessed", "What should Cashy do" in refused())
check("open_bank needs a bank", "Which bank" in refused(operation="open_bank"))
check("an unknown bank is refused", "Fifth Third and FNBO" in refused(operation="open_bank", bank="chase"))
check("a session that is a command is refused", "Cashy's sessions" in refused(operation="status", session="$(reboot)"))

from ..roles import (BRIDGE_ROLE_NAMES, ROLES, ROLE_BLURBS, ROLE_SPOKEN_NAMES,  # noqa: E402
                     ROLE_START_TASKS, TOYOTA_DESK_ROLE_NAMES)
from ..extra_roles import EXTRA_ROLES  # noqa: E402

check("the route is a defined role", "bridge_cashy" in ROLES and "bridge_cashy" in EXTRA_ROLES)
check("the route has a spoken name and a start task",
      "bridge_cashy" in ROLE_SPOKEN_NAMES and "bridge_cashy" in ROLE_START_TASKS)
check("the route is a Bridge role, so on both desks", "bridge_cashy" in BRIDGE_ROLE_NAMES)
check("the route is on Toyota's desk", "bridge_cashy" in TOYOTA_DESK_ROLE_NAMES)
check("the blurb says no password and no money", "password" in ROLE_BLURBS["bridge_cashy"]
      and "money" in ROLE_BLURBS["bridge_cashy"])

from ..adapters.cashy_route import CashyR46Route  # noqa: E402
from ..profiles import SYDNEY, TOYOTA  # noqa: E402

for profile in (TOYOTA, SYDNEY):
    def first_opinion(message):
        return profile.route_override.forced_agent(f"USER: {message}\nAGENT:")

    for message in ("Ask Cashy on R46 for her session status", "what is Cashy doing on Mom's computer",
                    'cashy-r46@cashy-r46: {"cashy":"status","machine":"R46","session":"r46-bridge"}',
                    "have Cashy open Fifth Third on Rosemary46", "what options does Cashy see on R46"):
        check(f"{profile.name}: {message!r} goes to bridge_cashy", first_opinion(message) == "bridge_cashy",
              first_opinion(message))
    for message in ("open Cashy", "what folders are on R46", "hello", "deploy agent_blocks on SQMC"):
        check(f"{profile.name}: {message!r} is not Cashy on R46", first_opinion(message) != "bridge_cashy",
              first_opinion(message))
check("only the latest message counts",
      CashyR46Route().forced_agent("USER: Cashy on R46 status\nAGENT: ok\nUSER: thanks\nAGENT:") is None)

if __name__ == "__main__":
    print(f"{len(PASSED)} passed")
