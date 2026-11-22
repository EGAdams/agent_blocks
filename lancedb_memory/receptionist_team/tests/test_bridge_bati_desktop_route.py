"""Toyota's route to BATI's desktop Chrome: form -> checked JSON prompt, the lane, the read-back, and the refusals.

Offline: no Bridge, no worker, no ADW, no model, no Windows. The prompts are compared against the worker's own
table (agent_bridge_worker/bati_desktop_agent.py: READS, WRITES, TAKES, FIELDS), imported rather than copied, so a
rename on either side fails here instead of being discovered when a task is refused on BATI.

    cd lancedb_memory
    .venv/bin/python -m receptionist_team.tests.test_bridge_bati_desktop_route
"""
import json

from agent_bridge_worker.bati_desktop_agent import FIELDS, READS, TAKES, WRITES

from ..bridge_client.bati_plans import BATI_PLANS
from ..bridge_client.models import BridgeAgent
from ..bridge_client.specialists import SPECIALISTS, FormProblem, SpecialistForm

BATI = BridgeAgent(name="bati-desktop", operations=["inspect", "implement"],
                   workspaces=["bati-desktop"], write_workspaces=["bati-desktop"])
PASSED = []


def check(name, condition, detail=""):
    assert condition, f"{name}: {detail}"
    PASSED.append(name)


def build(**form):
    return SPECIALISTS["bridge_bati_desktop"].builder.build(SpecialistForm.model_validate(form), BATI)


def refused(**form):
    try:
        build(**form)
    except FormProblem as problem:
        return problem.spoken
    raise AssertionError(f"{form} was accepted and should not have been")


def asked(request):
    return json.loads(request.prompt)


# -- wired to the bati-desktop agent only, with the worker's operations --------
spec = SPECIALISTS["bridge_bati_desktop"]
check("specialist sends only to the Bridge's bati-desktop agent", spec.bridge_agent == "bati-desktop", spec.bridge_agent)
check("the plans are exactly the worker's operations", set(BATI_PLANS) == set(READS) | set(WRITES),
      set(BATI_PLANS) ^ (set(READS) | set(WRITES)))

# -- reads need no yes; a step is read back and waits for one -------------------
for name in sorted(READS):
    req, said = build(operation=name)
    check(f"{name} is a read", (req.operation, req.approval_mode) == ("inspect", "read_only"), req.operation)
    check(f"{name} needs no yes", not req.needs_confirmation and not said)
    check(f"{name} goes to the bati-desktop workspace", req.workspace == "bati-desktop", req.workspace)
for name in sorted(WRITES):
    req, said = build(operation=name)
    check(f"{name} is a change", (req.operation, req.approval_mode) == ("implement", "workspace_write"))
    check(f"{name} is read back and waits for a yes", req.needs_confirmation and "BATI" in said, said)

# -- every prompt names BATI and only fields the worker accepts ------------------
for name in BATI_PLANS:
    body = asked(build(operation=name, url="http://localhost:8765/", view="plan-for-today")[0])
    check(f"{name} names its operation and machine BATI", body["bati"] == name and body["machine"] == "BATI", body)
    check(f"{name} sends no field the worker would refuse", set(body) <= FIELDS, set(body) - FIELDS)
    for key in ("url", "view"):
        if key in body:
            check(f"{name} carries {key} only where the worker takes it", name in TAKES[key], body)

# -- exact prompts --------------------------------------------------------------
check("status is exact", asked(build(operation="status")[0]) == {"bati": "status", "machine": "BATI"})
check("plan_for_today is exact", asked(build(operation="plan_for_today")[0]) == {"bati": "plan_for_today", "machine": "BATI"})
check("show_view defaults to the Plan for Today",
      asked(build(operation="show_view")[0]) == {"bati": "show_view", "machine": "BATI", "view": "plan-for-today"})
check("a spoken view is put right", asked(build(operation="read", view="Plan for Today")[0])["view"] == "plan-for-today")
check("a dashboard url passes", asked(build(operation="open", url="http://127.0.0.1:8765/")[0])["url"]
      == "http://127.0.0.1:8765/")
check("the machine cannot be chosen", asked(build(operation="status", machine="SQMC")[0])["machine"] == "BATI")
for spelling in ("plan for today", "bati.plan_for_today", "PLAN-FOR-TODAY"):
    check(f"{spelling} reaches the plan_for_today plan", asked(build(operation=spelling)[0])["bati"] == "plan_for_today")

# -- refusals say what to do instead -------------------------------------------
for word in ("type", "fill", "close", "quit", "click", "login"):
    check(f"{word} is answered in words", "Nothing types" in refused(operation=word))
check("a missing operation is not guessed at", "What should I do on BATI" in refused())
for spelling in ("shell", "navigate", "download"):
    check(f"{spelling} is not an operation", "What should I do on BATI" in refused(operation=spelling))
for bad in ("https://www.53.com/", "http://localhost:9222/json", "http://user:pw@localhost:8765/", "file:///etc/passwd",
            "javascript:alert(1)", "--help"):
    check(f"url {bad[:24]!r} is refused", "only opens the dashboard" in refused(operation="open", url=bad))
check("another view is refused", "only dashboard view" in refused(operation="show_view", view="finances"))

# -- the route is on Toyota's roster --------------------------------------------
from ..roles import ROLES, ROLE_SPOKEN_NAMES, ROLE_START_TASKS, TOYOTA_DESK_ROLE_NAMES  # noqa: E402
from ..extra_roles import EXTRA_ROLES  # noqa: E402
from ..profiles import BRIDGE_SPECIALISTS, TOYOTA  # noqa: E402
from ..adapters.bati_desktop_route import BatiDesktopRoute  # noqa: E402

check("the route is a defined role", "bridge_bati_desktop" in ROLES and "bridge_bati_desktop" in EXTRA_ROLES)
check("the route has a spoken name and a start task",
      "bridge_bati_desktop" in ROLE_SPOKEN_NAMES and "bridge_bati_desktop" in ROLE_START_TASKS)
check("the route is on Toyota's desk", "bridge_bati_desktop" in TOYOTA_DESK_ROLE_NAMES)
check("the route reads back and waits for a yes", "bridge_bati_desktop" in BRIDGE_SPECIALISTS)


def first_opinion(message):
    return TOYOTA.route_override.forced_agent(f"USER: {message}\nAGENT:")


for message in ("show the plan for today on BATI", "open the dashboard in Chrome on BATI", "take a screenshot of BATI's screen",
                "is the debug Chrome up on BATI's desktop", "what's on BATI's browser tab"):
    check(f"{message!r} goes to BATI's desktop", BatiDesktopRoute().forced_agent(f"USER: {message}\nAGENT:")
          == "bridge_bati_desktop")
    check(f"{message!r} wins Toyota's route panel", first_opinion(message) == "bridge_bati_desktop", first_opinion(message))
for message in ("is BATI up", "file the last scan on BATI under insurance/home", "open chrome on SQMC",
                "how much did we spend on BATI's screen", "status of t_0123456789abcdef01234567 on BATI's desktop",
                "hello Toyota"):
    check(f"{message!r} is not a BATI desktop step", BatiDesktopRoute().forced_agent(f"USER: {message}\nAGENT:") is None)
check("only the latest message counts",
      BatiDesktopRoute().forced_agent("USER: show BATI's screen\nAGENT: ok\nUSER: thanks\nAGENT:") is None)

if __name__ == "__main__":
    print(f"{len(PASSED)} passed")
