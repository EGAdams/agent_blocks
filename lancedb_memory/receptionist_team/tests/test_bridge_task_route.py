"""BridgeTaskRoute: a question about a Bridge task id is read back, not answered from memory.

Pins 2026-11-05: after a restart, "What is the result of Star Trek Bridge task t_8a71...?"
was answered from recalled conversation (wrong task, stale state) and never reached the Bridge.

    .venv/bin/python -m receptionist_team.tests.test_bridge_task_route
"""
from ..adapters.bridge_task_route import BridgeTaskRoute
from ..profiles import TOYOTA

TID = "t_" + "8a7119bac894a2e2f85a0d06"
ROUTE = BridgeTaskRoute()


def _prompt(message, recalled=""):
    return f"{recalled}\n\nUSER: {message}\nAGENT:"


def test_result_status_and_cancel_questions_go_to_the_bridge_desk():
    for line in (f"What is the result of Star Trek Bridge task {TID}?", f"status {TID}",
                 f"Is task {TID} still running?", f"cancel {TID}", TID):
        assert ROUTE.forced_agent(_prompt(line)) == "star_trek_bridge", line
        assert ROUTE.question(_prompt(line)) == line


def test_new_questions_and_recalled_ids_have_no_opinion():
    assert ROUTE.forced_agent(_prompt(f"explain how the gateway assigns ids like {TID}")) is None
    assert ROUTE.forced_agent(_prompt("status")) is None  # no id: the model decides, as before
    assert ROUTE.forced_agent(_prompt("what's on the agenda?", recalled=f"USER: result {TID}")) is None


def test_toyota_asks_this_route_before_the_device_and_figure_routes():
    # "what did the printer task t_... say" names a printer, but it is a readback.
    line = f"what did the printer task {TID} say?"
    assert TOYOTA.route_override.forced_agent(_prompt(line)) == "star_trek_bridge"


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for name, test in tests:
        test()
        print("PASS ", name)
    print(f"\n{len(tests)}/{len(tests)} passed")
