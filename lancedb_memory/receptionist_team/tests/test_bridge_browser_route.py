"""Toyota's route to Browser Control: form -> checked JSON prompt, the lane, the read-back, and the refusals.

Offline: no Bridge, no Chrome, no model. The point of these is that Toyota reaches the *same* operations ChatGPT
drives -- the prompts here are compared against the worker's own table (agent_bridge_worker/browser_agent.py), so a
rename on either side fails a test instead of being discovered on a live page.

    cd lancedb_memory
    .venv/bin/python -m receptionist_team.tests.test_bridge_browser_route
"""
import json

from ..bridge_client.browser_plans import BROWSER_PLANS
from ..bridge_client.models import BridgeAgent
from ..bridge_client.specialists import SPECIALISTS, FormProblem, SpecialistForm

BROWSER = BridgeAgent(name="browser", operations=["inspect", "implement"],
                      workspaces=["browser"], write_workspaces=["browser"])
PASSED = []


def check(name, condition, detail=""):
    assert condition, f"{name}: {detail}"
    PASSED.append(name)


def build(**form):
    return SPECIALISTS["bridge_browser"].builder.build(SpecialistForm.model_validate(form), BROWSER)


def refused(**form):
    """The spoken reason the form didn't become a request."""
    try:
        build(**form)
    except FormProblem as problem:
        return problem.spoken
    raise AssertionError(f"{form} was accepted and should not have been")


def asked(request):
    return json.loads(request.prompt)


# -- the specialist is wired, and only to the browser agent ---------------------
spec = SPECIALISTS["bridge_browser"]
check("specialist sends only to the Bridge's browser agent", spec.bridge_agent == "browser", spec.bridge_agent)
check("the live operation list is the plan registry", spec.builder.context(BROWSER).startswith("Operations: status"))

# -- the lanes: a read can never be sent as a change, or the other way ----------
for name in ("status", "tabs", "read", "form_controls"):
    req, _ = build(operation=name)
    check(f"{name} is a read", (req.operation, req.approval_mode) == ("inspect", "read_only"), req.operation)
    check(f"{name} needs no yes", not req.needs_confirmation)
for name in ("open", "dashboard_notes", "refresh", "click", "select_option", "fill"):
    form = {"operation": name}
    form |= {"click": {"css": "#b"}, "select_option": {"css": "#d", "option_text": "x"},
             "fill": {"css": "#t", "text": "x"}}.get(name, {})
    req, said = build(**form)
    check(f"{name} is a change", (req.operation, req.approval_mode) == ("implement", "workspace_write"))
    check(f"{name} is read back and waits for a yes", req.needs_confirmation and said.strip() != "", said)

# -- the prompt is exactly what the worker's own table accepts ------------------
# Mirrors agent_bridge_worker/browser_agent.py: READS / WRITES, FLAGS, REQUIRED.
WORKER_READS = {"status", "tabs", "read", "screenshot", "form_controls"}
WORKER_WRITES = {"open", "navigate", "refresh", "select_tab", "click", "close", "dashboard_notes",
                 "select_option", "fill"}
WORKER_FIELDS = {"browser", "confirm", "target", "url", "tab", "text", "label", "css", "option_text", "value", "limit"}
for name, plan in BROWSER_PLANS.items():
    lane = "inspect" if name in WORKER_READS else "implement" if name in WORKER_WRITES else None
    check(f"the worker offers {name}", lane is not None, "the worker's table has no such operation")
    check(f"{name} is sent in the worker's lane", plan.operation == lane, f"{plan.operation} != {lane}")

req, _ = build(operation="form_controls", tab="A1B2C3", limit=120)
check("form_controls carries the tab and the limit",
      asked(req) == {"browser": "form_controls", "tab": "A1B2C3", "limit": 120}, asked(req))
req, _ = build(operation="select_option", css="#ddlType", option_text="PLAT Records", tab="A1B2C3")
check("select_option carries the selector and what the item says",
      asked(req) == {"browser": "select_option", "css": "#ddlType", "option_text": "PLAT Records", "tab": "A1B2C3"},
      asked(req))
req, _ = build(operation="fill", css="#txtName", text="FRALICK")
check("fill carries the selector and the words to type",
      asked(req) == {"browser": "fill", "css": "#txtName", "text": "FRALICK"}, asked(req))
for name, plan in BROWSER_PLANS.items():
    form = {"operation": name, "css": "#x", "option_text": "x", "text": "x", "url": "https://example.com",
            "tab": "A1B2C3", "limit": 5}  # every field at once: no plan may pass one the worker refuses
    req, _ = build(**form)
    unknown = set(asked(req)) - WORKER_FIELDS
    check(f"{name} sends no field the worker would refuse", not unknown, unknown)

# -- an empty fill is how a field is emptied, and it survives the JSON ----------
req, said = build(operation="fill", css="#txtName", text="")
check("an empty fill keeps its text field", asked(req) == {"browser": "fill", "css": "#txtName", "text": ""}, asked(req))
check("emptying a field says so", "clear" in said.lower(), said)

# -- a guessed selector is never sent: it asks for form_controls instead --------
for what in ("select_option", "fill", "click"):
    form = {"operation": what, "label": None, "css": None}
    form |= {"select_option": {"option_text": "x"}, "fill": {"text": "x"}}.get(what, {})
    spoken = refused(**form)
    check(f"{what} with no control asks to list the form first", "form's controls" in spoken, spoken)
spoken = refused(operation="fill", css="the project name box", text="x")
check("a sentence is not a selector", "list the form's controls" in spoken, spoken)
req, _ = build(operation="fill", label="Project Name", text="x")
check("a label is accepted when there is no selector", asked(req).get("label") == "Project Name", asked(req))

# -- a credential is refused before it is ever sent -----------------------------
for text in ("hunter2password", "my PIN is 4821", "the one-time code 889123", "sk-apikey-xyz"):
    spoken = refused(operation="fill", css="#x", text=text)
    check(f"a credential in the text is refused ({text[:12]})", "never type one into a page" in spoken, spoken)
spoken = refused(operation="fill", label="Password", text="whatever")
check("a password field is refused by its label", "never type one into a page" in spoken, spoken)
req, _ = build(operation="fill", css="#x", text="Fralick's Addition")
check("ordinary words are not mistaken for a secret", asked(req)["text"] == "Fralick's Addition")
# A guard that refuses an ordinary sentence teaches the caller to stop believing its refusals, so each of
# these is a word the pattern used to match on a bare prefix: "pass", "pin", "token", "one time".
for text in ("keep passing this along", "pin the tab", "tokenomics is not a word I like",
             "pass the October statement to Mazda", "one time I saw that"):
    req, _ = build(operation="fill", css="#x", text=text)
    check(f"an ordinary sentence is not refused as a secret ({text[:14]})", asked(req)["text"] == text)

# -- confirm is never set, so a consequential click stays the ADW's refusal -----
for name in BROWSER_PLANS:
    req, _ = build(operation=name, css="#x", option_text="x", text="x", url="https://example.com", tab="A1B2C3")
    check(f"{name} never carries confirm", "confirm" not in asked(req), asked(req))
_, said = build(operation="click", css="#btnPay")
check("the click read-back says a consequential one will be refused", "refuse it" in said, said)

# -- the other refusals say what to do instead ---------------------------------
check("an unknown operation lists what there is", "form controls" in refused(operation="screenshot"))
check("a missing operation is not guessed at", "What should I do in the browser" in refused(instructions="do stuff"))
check("navigate needs somewhere to go", "https://" in refused(operation="navigate"))
check("a made-up address is refused", "web address" in refused(operation="navigate", url="javascript:alert(1)"))
check("a file url is refused", "web address" in refused(operation="navigate", url="file:///etc/passwd"))
check("select_tab needs a tab", "Which tab" in refused(operation="select_tab"))
check("a made-up tab id is refused", "tab id" in refused(operation="select_tab", tab="the second one"))
check("select_option needs an item", "Which item" in refused(operation="select_option", css="#d"))
check("a limit out of range is refused", "1 to 200" in refused(operation="form_controls", limit=5000))
check("too much text for one field is refused", "under 500" in refused(operation="fill", css="#x", text="z" * 501))
check("closing a tab says what is lost", "not saved is lost" in build(operation="close", tab="A1B2C3")[1])

# -- the spelling the model may use ---------------------------------------------
for spelling in ("form_controls", "browser.form_controls", "browser_form_controls", "FORM_CONTROLS"):
    req, _ = build(operation=spelling)
    check(f"{spelling} reaches the same plan", asked(req)["browser"] == "form_controls", asked(req))

# -- the route is on Toyota's roster and nobody else's by accident --------------
from ..roles import ROLES, ROLE_BLURBS, ROLE_SPOKEN_NAMES, ROLE_START_TASKS, TOYOTA_DESK_ROLE_NAMES  # noqa: E402
from ..extra_roles import EXTRA_ROLES  # noqa: E402

check("the route is a defined role", "bridge_browser" in ROLES and "bridge_browser" in EXTRA_ROLES)
check("the route has a spoken name and a start task",
      "bridge_browser" in ROLE_SPOKEN_NAMES and "bridge_browser" in ROLE_START_TASKS)
check("the route is on Toyota's desk", "bridge_browser" in TOYOTA_DESK_ROLE_NAMES)
check("the blurb says a password is never typed", "never types a password" in ROLE_BLURBS["bridge_browser"])

if __name__ == "__main__":
    print(f"{len(PASSED)} passed")
