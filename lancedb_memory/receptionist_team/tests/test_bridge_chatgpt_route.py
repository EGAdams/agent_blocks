"""Toyota's route to the ChatGPT relay: form -> checked JSON prompt, the lane, the read-back, and the refusals.

Offline: no Bridge, no Chrome, no ChatGPT, no model. The point of these is that Toyota reaches the *same*
operations ChatGPT drives -- the prompts here are compared against the worker's own table
(agent_bridge_worker/chatgpt_relay_agent.py), so a rename on either side fails a test instead of being
discovered when a message does not arrive.

    cd lancedb_memory
    .venv/bin/python -m receptionist_team.tests.test_bridge_chatgpt_route
"""
import json

from ..bridge_client.models import BridgeAgent
from ..bridge_client.relay_plans import MAX_HOPS, MAX_LAST, MAX_MESSAGE, RELAY_PLANS
from ..bridge_client.specialists import SPECIALISTS, FormProblem, SpecialistForm

RELAY = BridgeAgent(name="chatgpt-relay", operations=["inspect", "implement"],
                    workspaces=["chatgpt-relay"], write_workspaces=["chatgpt-relay"])
PASSED = []


def check(name, condition, detail=""):
    assert condition, f"{name}: {detail}"
    PASSED.append(name)


def build(**form):
    return SPECIALISTS["bridge_chatgpt"].builder.build(SpecialistForm.model_validate(form), RELAY)


def refused(**form):
    """The spoken reason the form didn't become a request."""
    try:
        build(**form)
    except FormProblem as problem:
        return problem.spoken
    raise AssertionError(f"{form} was accepted and should not have been")


def asked(request):
    return json.loads(request.prompt)


# -- the specialist is wired, and only to the relay agent -----------------------
spec = SPECIALISTS["bridge_chatgpt"]
check("specialist sends only to the Bridge's relay agent", spec.bridge_agent == "chatgpt-relay", spec.bridge_agent)
check("the live operation list is the plan registry", spec.builder.context(RELAY).startswith("Operations: instances"))

# -- the lanes: a message can never be sent as a read, or the other way ---------
for name in ("instances", "health", "ready", "read"):
    req, _ = build(operation=name)
    check(f"{name} is a read", (req.operation, req.approval_mode) == ("inspect", "read_only"), req.operation)
    check(f"{name} needs no yes", not req.needs_confirmation)
for name, extra in (("ask", {"message": "hello"}), ("type", {"message": "hello"}), ("send", {}), ("open", {})):
    req, said = build(operation=name, **extra)
    check(f"{name} is a change", (req.operation, req.approval_mode) == ("implement", "workspace_write"))
    check(f"{name} is read back and waits for a yes", req.needs_confirmation and said.strip() != "", said)

# -- the prompt is exactly what the worker's own table accepts ------------------
# Mirrors agent_bridge_worker/chatgpt_relay_agent.py: READS / WRITES, FLAGS, SWITCHES, REQUIRED.
WORKER_READS = {"instances", "health", "ready", "read"}
WORKER_WRITES = {"open", "new_chat", "type", "send", "ask"}
WORKER_FIELDS = {"relay", "instance", "message", "url", "last", "settle", "timeout", "stable_seconds",
                 "hops", "origin", "confirm", "no_wait"}
for name, plan in RELAY_PLANS.items():
    lane = "inspect" if name in WORKER_READS else "implement" if name in WORKER_WRITES else None
    check(f"the worker offers {name}", lane is not None, "the worker's table has no such operation")
    check(f"{name} is sent in the worker's lane", plan.operation == lane, f"{plan.operation} != {lane}")

req, _ = build(operation="ask", instance="bati", message="what did we decide about the plat?")
check("ask carries the session and the message",
      asked(req) == {"relay": "ask", "instance": "bati", "message": "what did we decide about the plat?"}, asked(req))
req, _ = build(operation="read", instance="bati", last=2)
check("read carries the session and how many turns",
      asked(req) == {"relay": "read", "instance": "bati", "last": 2}, asked(req))
req, _ = build(operation="send", instance="bati")
check("send carries nothing but the session", asked(req) == {"relay": "send", "instance": "bati"}, asked(req))
req, _ = build(operation="ask", message="hi", hops=2, origin="bati", settle="new_turn")
check("a forwarded message carries its origin and hop count",
      asked(req) == {"relay": "ask", "message": "hi", "settle": "new_turn", "hops": 2, "origin": "bati"}, asked(req))
for name in RELAY_PLANS:
    if name == "new_chat":
        continue                 # always refused; see below
    form = {"operation": name, "instance": "bati", "message": "x", "url": "https://chatgpt.com/",
            "last": 5, "settle": "none", "hops": 1, "origin": "zed"}   # every field at once
    req, _ = build(**form)
    unknown = set(asked(req)) - WORKER_FIELDS
    check(f"{name} sends no field the worker would refuse", not unknown, unknown)

# -- a session name is read off `instances`, never invented --------------------
for name in ("the other one", "https://chatgpt.com/", "bati and sydney"):
    spoken = refused(operation="ask", instance=name, message="hi")
    check(f"a made-up session name is refused ({name[:12]})", "which sessions" in spoken, spoken)
req, _ = build(operation="ask", message="hi")
check("no session means the relay's own default", "instance" not in asked(req), asked(req))

# -- a credential is refused before it is ever sent -----------------------------
# Worse here than on a web form: that account keeps its history, so a secret sent once is sent for good.
for text in ("hunter2password", "my PIN is 4821", "the one-time code 889123", "sk-apikey-xyz"):
    spoken = refused(operation="ask", message=text)
    check(f"a credential in the message is refused ({text[:12]})", "history for good" in spoken, spoken)
for text in ("keep passing this along", "pin the tab", "tokenomics is not a word I like",
             "pass the October statement to Mazda", "one time I saw that"):
    req, _ = build(operation="ask", message=text)
    check(f"an ordinary sentence is not refused as a secret ({text[:14]})", asked(req)["message"] == text)

# -- confirm is never set, so new_chat stays refused ---------------------------
for name in RELAY_PLANS:
    if name == "new_chat":
        continue
    req, _ = build(operation=name, instance="bati", message="x", url="https://chatgpt.com/", last=5, hops=1)
    check(f"{name} never carries confirm", "confirm" not in asked(req), asked(req))
spoken = refused(operation="new_chat")
check("a new chat is refused from this desk", "throws away the thread" in spoken, spoken)
spoken = refused(operation="new_chat", instance="bati")
check("a new chat is refused even with a session named", "yourself in the window" in spoken, spoken)

# -- nothing here can close that browser ---------------------------------------
# It is signed in to EG's account; closing it costs the login, so no plan and no spelling reaches one.
check("no plan closes the browser", not {"quit", "close", "shutdown", "logout"} & set(RELAY_PLANS))
for spelling in ("quit", "relay.quit", "chatgpt_quit", "close", "shutdown", "logout"):
    spoken = refused(operation=spelling)
    check(f"{spelling} is not an operation", "What should I do with ChatGPT" in spoken, spoken)

# -- send never retypes, because that would post the message twice -------------
_, said = build(operation="send", instance="bati")
check("the send read-back says it does not retype", "without retyping" in said, said)
_, said = build(operation="type", instance="bati", message="one moment")
check("typing says nothing is sent yet", "Nothing is sent" in said, said)
_, said = build(operation="ask", instance="bati", message="hi")
check("ask says it waits for the reply to finish", "once it finishes" in said, said)
_, said = build(operation="ask", instance="bati", message="hi", no_wait=True)
check("no_wait says plainly that no answer is coming back", "not wait for an answer" in said, said)
_, said = build(operation="open", instance="bati")
check("open says a password is never typed", "never type a password" in said, said)

# -- the other refusals say what to do instead ---------------------------------
check("a missing operation is not guessed at", "What should I do with ChatGPT" in refused(instructions="do stuff"))
check("an empty message is refused", "What should I say" in refused(operation="ask"))
check("a message of spaces is refused", "What should I say" in refused(operation="ask", message="   "))
check(f"a message over {MAX_MESSAGE} characters is refused",
      str(MAX_MESSAGE) in refused(operation="ask", message="z" * (MAX_MESSAGE + 1)))
check("too many turns to read is refused", f"1 to {MAX_LAST}" in refused(operation="read", last=5000))
check("too many relay hops is refused",
      str(MAX_HOPS) in refused(operation="ask", message="hi", hops=MAX_HOPS + 1))
check("an unknown settle rule is refused", "stop changing" in refused(operation="ask", message="hi", settle="soon"))
check("a made-up address is refused", "web address" in refused(operation="open", url="javascript:alert(1)"))
check("a file url is refused", "web address" in refused(operation="open", url="file:///etc/passwd"))
check("a made-up origin is refused", "sessions" in refused(operation="ask", message="hi", origin="over there"))

# -- the spelling the model may use ---------------------------------------------
for spelling in ("ask", "relay.ask", "chatgpt_ask", "ASK"):
    req, _ = build(operation=spelling, message="hi")
    check(f"{spelling} reaches the same plan", asked(req)["relay"] == "ask", asked(req))

# -- the route is on Toyota's roster and nobody else's by accident --------------
from ..roles import ROLES, ROLE_BLURBS, ROLE_SPOKEN_NAMES, ROLE_START_TASKS, TOYOTA_DESK_ROLE_NAMES  # noqa: E402
from ..extra_roles import EXTRA_ROLES  # noqa: E402

check("the route is a defined role", "bridge_chatgpt" in ROLES and "bridge_chatgpt" in EXTRA_ROLES)
check("the route has a spoken name and a start task",
      "bridge_chatgpt" in ROLE_SPOKEN_NAMES and "bridge_chatgpt" in ROLE_START_TASKS)
check("the route is on Toyota's desk", "bridge_chatgpt" in TOYOTA_DESK_ROLE_NAMES)
check("the blurb says that browser is never closed", "never closes that browser" in ROLE_BLURBS["bridge_chatgpt"])

if __name__ == "__main__":
    print(f"{len(PASSED)} passed")
