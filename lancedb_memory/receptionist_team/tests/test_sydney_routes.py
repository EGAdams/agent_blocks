"""Sydney's forced routes: a named specialist is used, plain Bridge verbs reach their specialist, and the
ambiguous phrasings stay with the model. Toyota's panel is unchanged. Offline: no model, no network.

    cd lancedb_memory
    .venv/bin/python -m receptionist_team.tests.test_sydney_routes
"""
from ..adapters.bridge_verb_route import verb_route
from ..adapters.cashy_route import cashy_r46_request
from ..adapters.named_route import NamedRoute
from ..profiles import SYDNEY, TOYOTA

TID = "t_" + "abcdef0123456789abcdef01"


def _turn(text):
    return f"\n\nUSER: {text}\nAGENT:"


def _sydney(text):
    return SYDNEY.route_override.forced_agent(_turn(text))


def _toyota(text):
    return TOYOTA.route_override.forced_agent(_turn(text))


async def test_a_named_specialist_is_used():
    # 2026-11-17: this one reached the read-only desk, which could only refuse it.
    assert _sydney("Use bridge_cashy to list the options on R46") == "bridge_cashy"
    assert _sydney("Use bridge_chatgpt to ask how tall Everest is") == "bridge_chatgpt"
    assert _sydney("Use bridge_deploy to check dashboard health") == "bridge_deploy"
    assert _sydney("use the bridge_browser to list my tabs") == "bridge_browser"
    assert _sydney("bridge_notes: show the deed") == "bridge_notes"
    assert _sydney("Send it to sydney_post_office please") == "sydney_post_office"


async def test_a_name_merely_mentioned_or_two_names_force_nothing():
    named = NamedRoute(["bridge_cashy", "bridge_browser", "bridge_scala_storage", "star_trek_bridge"])
    assert named.named("Explain how bridge_cashy works") is None
    assert named.named("use bridge_cashy or bridge_browser, whichever") is None
    assert named.named("use bridge_scala_storage to list folders") == "bridge_scala_storage"
    assert named.named("") is None


async def test_a_name_off_her_roster_is_not_forced():
    assert _sydney("Use live_books to tell me the total") is None   # Toyota's specialist, not Sydney's


async def test_bridge_cashy_counts_as_naming_cashy():
    assert cashy_r46_request("Use bridge_cashy to list the options on R46")
    assert not cashy_r46_request("Open Cashy")


async def test_plain_browser_verbs_reach_the_bridge_browser():
    for text in ("Download https://www.irs.gov/pub/irs-pdf/fw8ben.pdf", "Navigate the browser to https://example.com",
                 "What tabs are open in my browser?", "Read the page in the current tab",
                 'Click the "Learn more" link', "Fill the search box with plat 12", "Close the example.com tab",
                 "Pick Florida from the state dropdown"):
        assert _sydney(text) == "bridge_browser", text


async def test_relay_deploy_and_storage_wording():
    assert _sydney("Ask ChatGPT what the capital of France is") == "bridge_chatgpt"
    assert _sydney("Is the ChatGPT relay up?") == "bridge_chatgpt"
    assert _sydney("Deploy agent_blocks") == "bridge_deploy"
    assert _sydney("Check the health of the dashboard deployment") == "bridge_deploy"
    assert _sydney("Show me the dashboard logs") == "bridge_deploy"
    assert _sydney("Which storage machines are reachable?") == "bridge_scala_storage"


async def test_ambiguous_wording_stays_with_the_model():
    for text in ("Open https://example.com in my Chrome",   # her browser_opener opens links
                 "Open Cashy", "Explain how bridge_cashy works", "Download the October 2025 Chase statement",
                 "How do I add a link to the dashboard?", "What do you know about ChatGPT?", "Write a new agent for me",
                 "yes"):
        assert _sydney(text) is None, text


async def test_earlier_routes_still_win():
    assert _sydney("Ask Sonnet to write code that clicks the login button") == "sydney_post_office"
    assert _sydney(f"What's the status of {TID}?") == "star_trek_bridge"
    assert verb_route(f"download the file from task {TID}") is None
    assert verb_route("What did we spend on downloads of pdf files?") is None


async def test_toyota_gets_none_of_the_verb_routes():
    for text in ("Download https://www.irs.gov/pub/irs-pdf/fw8ben.pdf", "Ask ChatGPT what the capital of France is",
                 "Deploy agent_blocks", "Use bridge_deploy to check dashboard health"):
        assert _toyota(text) is None, text
    assert _toyota("Use bridge_cashy to list the options on R46") == "bridge_cashy"   # the Cashy fix is shared



# -- 2026-11-22: "which service?" and "ask Sonnet" ---------------------------------------------------------------
def _after(agent_said, text):
    return f"\n\nUSER: earlier\nAGENT: {agent_said}\nUSER: {text}\nAGENT:"


async def test_a_plain_scan_request_reaches_the_scanner():
    # Live: no route caught this, and she answered from Nov 4's "which systemd service?" turns.
    assert _sydney("Have Scala run the scanner again so we can find out what's in it.") == "bridge_scanner"
    assert _sydney("scan what's in the freezer scanner") == "bridge_scanner"
    assert _sydney("What's in the scanner?") == "bridge_scanner"
    assert _sydney("What did we spend on scanner paper?") is None


async def test_sonnet_is_asked_rather_than_given_work():
    for text in ("you'll have to ask somebody from the Star Trek bridge, like maybe Sonnet knows.",
                 "Ask Sonnet what the scanner service is called.",
                 "get that information from Sonnet", "Check with Claude Sonnet"):
        assert _sydney(text) == "ask_sonnet", text
    assert _sydney("Ask Sonnet to fix the scanner") != "ask_sonnet"
    assert _sydney("Sonnet knows how to build it; have him implement the button") != "ask_sonnet"


async def test_the_question_sonnet_gets_carries_what_she_had_asked():
    prompt = _after("Which systemd service should I restart for the freezer scanner?", "Sonnet knows")
    panel = SYDNEY.route_override
    assert panel.forced_agent(prompt) == "ask_sonnet"
    asked = panel.question(prompt)
    assert "Which systemd service should I restart for the freezer scanner?" in asked, asked


async def test_a_bare_scanner_choice_answers_her_question():
    prompt = _after("Which scanner should I use, the window or the freezer?", "freezer")
    panel = SYDNEY.route_override
    assert panel.forced_agent(prompt) == "bridge_scanner"
    assert panel.question(prompt) == "scan on the freezer scanner"
    assert panel.forced_agent(_after("Which printer?", "freezer")) is None
    assert panel.forced_agent(_after("Which scanner should I use?", "the window one")) == "bridge_scanner"


async def test_toyota_keeps_her_routes_for_these():
    assert _toyota("Have Scala run the scanner again so we can find out what's in it.") == "scala_questions"   # hers, as before
    assert _toyota("Ask Sonnet what the scanner service is called.") != "ask_sonnet"


if __name__ == "__main__":
    from tests.runner import main

    main(dict(globals()))
