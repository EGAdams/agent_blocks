"""The relay's operations over fakes: the table, each refusal, and the seven-step ask.

Nothing here touches the network, Chrome or a ChatGPT account.

    cd adws && uv run --quiet --with pydantic --with pytest pytest -q chatgpt_relay/tests
"""
from __future__ import annotations

import pytest

from chatgpt_relay.catalog import PUBLIC, relay_operations
from chatgpt_relay.guards import default_guards
from chatgpt_relay.instances import DEFAULT_LABEL, InstanceUnknown, StaticInstanceCatalog
from chatgpt_relay.interfaces import IRelayTransport
from chatgpt_relay.observers import RecordingObserver
from chatgpt_relay.plane import RelayPlane, SettleFactory

from .fakes import FakeClock, FakeTransport, thread


def make(**transport_args):
    transport = FakeTransport(**transport_args)
    clock = FakeClock()
    observer = RecordingObserver()
    plane = RelayPlane(catalog=StaticInstanceCatalog(), transport=transport, guards=default_guards(),
                       settles=SettleFactory(clock), clock=clock, observer=observer)
    return relay_operations(plane), transport, observer


def run(ops, public_name, **args):
    operation = ops[PUBLIC[public_name]]
    return operation.run(args)


ANSWERED = [thread(("user", "q")), thread(("user", "q"), ("assistant", "Kent County")),
            thread(("user", "q"), ("assistant", "Kent County"))]


# -- the table -------------------------------------------------------------------
def test_every_public_name_resolves_to_an_operation_in_the_table():
    ops, _, _ = make()
    assert set(PUBLIC.values()) == set(ops)
    for public, internal in PUBLIC.items():
        assert public.startswith("relay.") and ops[internal].name == internal


def test_the_table_has_no_way_to_quit_the_browser_anywhere_in_it():
    """Quitting that Chrome costs a person their signed-in session, so the capability does not exist --
    not in the table, not on the transport interface."""
    ops, _, _ = make()
    assert not [name for name in ops if "quit" in name]
    assert not hasattr(IRelayTransport, "quit")


# -- reading, and the one operation that works when everything is asleep ----------
def test_instances_answers_without_touching_the_network():
    ops, transport, _ = make(unreachable={"health", "read_thread", "type", "send"})
    out = run(ops, "relay.instances")
    assert out.ok and transport.calls == []
    assert DEFAULT_LABEL in out.line and "owner EG" in out.line


def test_health_reports_a_reachable_session_and_whether_its_chrome_is_up():
    ops, _, _ = make(health={"status": "ok", "browser_started": False})
    out = run(ops, "relay.health")
    assert out.ok and "not started yet" in out.line and out.data["browser_started"] is False


def test_an_unreachable_session_says_so_and_names_what_to_check():
    ops, _, _ = make(unreachable={"health"})
    out = run(ops, "relay.health")
    assert not out.ok and "did not answer" in out.line


def test_an_unknown_instance_is_refused_with_the_list_of_what_exists():
    ops, _, _ = make()
    out = run(ops, "relay.health", instance="nope")
    assert not out.ok and DEFAULT_LABEL in out.line


def test_an_alias_reaches_the_same_session():
    ops, _, _ = make()
    for spelling in ("default", "chatgpt", "shdbati", None):
        assert run(ops, "relay.health", instance=spelling).ok


def test_ready_refuses_a_session_whose_chrome_has_not_started_and_names_what_to_run():
    """Typing into a Chrome that is not up gets `prompt_not_found`, which reads like a broken relay.
    `relay.health` reports the same state as information; `relay.ready` reports it as a refusal."""
    ops, _, _ = make(health={"status": "ok", "browser_started": False})
    out = run(ops, "relay.ready")
    assert not out.ok and "relay.open" in out.line and out.data["browser_started"] is False
    assert run(ops, "relay.health").ok          # the same state, as a diagnostic


def test_ready_does_not_open_the_page_itself_because_that_abandons_the_persons_thread():
    ops, transport, _ = make(health={"status": "ok", "browser_started": False})
    run(ops, "relay.ready")
    assert "open" not in transport.calls


def test_open_lands_on_chatgpt_by_default_and_says_what_is_left_behind():
    ops, _, _ = make()
    out = run(ops, "relay.open")
    assert out.ok and out.data["url"] == "https://chatgpt.com/" and "left behind" in out.line


def test_open_refuses_anything_that_is_not_a_web_address():
    ops, transport, _ = make()
    for url in ("javascript:alert(1)", "file:///etc/passwd", "chatgpt.com"):
        assert not run(ops, "relay.open", url=url).ok, url
    assert "open" not in transport.calls


def test_ask_is_gated_on_ready_and_not_on_health():
    """A relay that types into a dead Chrome reports a confusing server error; one that checks first
    reports the actual problem."""
    ops, transport, _ = make(script=ANSWERED, health={"status": "ok", "browser_started": False})
    out = run(ops, "relay.ask", message="hello")
    assert not out.ok and "relay.open" in out.line
    assert transport.typed == [] and "send" not in transport.calls


def test_read_reports_the_turns_and_the_thread_length():
    ops, _, _ = make(script=[thread(("user", "hello"), ("assistant", "hi"), count=47)])
    out = run(ops, "relay.read")
    assert out.ok and "47 turns" in out.line and out.data["turn_count"] == 47
    assert [t["role"] for t in out.data["turns"]] == ["user", "assistant"]


def test_an_empty_thread_is_reported_as_empty_rather_than_as_a_failure():
    ops, _, _ = make(script=[thread()])
    out = run(ops, "relay.read")
    assert out.ok and "empty" in out.line and out.data["turns"] == []


def test_read_refuses_a_turn_count_it_cannot_use():
    ops, _, _ = make(script=[thread()])
    assert not run(ops, "relay.read", last=0).ok
    assert not run(ops, "relay.read", last="some").ok


# -- new_chat: the golden rule, in code ------------------------------------------
def test_a_new_chat_is_refused_without_confirmation_and_nothing_is_changed():
    ops, transport, _ = make()
    out = run(ops, "relay.new_chat")
    assert not out.ok and "leaves the current thread's context behind" in out.line
    assert transport.calls == []


def test_a_confirmed_new_chat_runs():
    ops, transport, _ = make()
    out = run(ops, "relay.new_chat", confirm=True)
    assert out.ok and "new, empty thread" in out.line and "new_chat" in transport.calls


# -- typing and sending ----------------------------------------------------------
def test_type_puts_the_message_in_the_box_and_says_it_was_not_sent():
    ops, transport, _ = make()
    out = run(ops, "relay.type", message="what county is Fralick's Addition in?")
    assert out.ok and "not sent" in out.line
    assert transport.typed == ["what county is Fralick's Addition in?"]
    assert "send" not in transport.calls


def test_a_credential_is_refused_before_the_transport_is_reached_at_all():
    ops, transport, _ = make()
    out = run(ops, "relay.type", message="the password is hunter2")
    assert not out.ok and out.data["guard"] == "credential"
    assert transport.calls == [] and transport.typed == []


def test_the_message_argument_is_redacted_from_whatever_logs_the_run():
    """The lifecycle log records every argument. One of them is somebody's message."""
    ops, _, _ = make()
    for name in ("relay_type", "relay_guard", "relay_ask"):
        hidden = list(getattr(ops[name], "redact", ())) + [k for s in getattr(ops[name], "steps", []) or []
                                                           for k in getattr(s, "redact", ())]
        assert "message" in hidden, name


def test_typing_without_a_message_asks_for_one():
    ops, _, _ = make()
    assert "--message" in run(ops, "relay.type").line


def test_the_servers_own_words_for_success_are_not_mistaken_for_failure():
    """Each endpoint answers in its own vocabulary -- "typed", "sent", "opened" -- and the relay's first
    live run reported "typed" to the operator as "did not take the text"."""
    ops, _, _ = make()
    assert run(ops, "relay.type", message="hello").ok
    assert run(ops, "relay.send").ok
    assert run(ops, "relay.open").ok
    assert run(ops, "relay.new_chat", confirm=True).ok


def test_a_server_that_would_not_take_the_text_says_nothing_was_sent():
    ops, _, _ = make(type_status="error")
    out = run(ops, "relay.type", message="hello")
    assert not out.ok and "Nothing was sent" in out.line


def test_send_reports_that_the_reply_has_not_been_read_yet():
    ops, _, _ = make()
    out = run(ops, "relay.send")
    assert out.ok and "has not been read yet" in out.line


# -- the snapshot, the settle and the answer -------------------------------------
def test_the_snapshot_records_the_thread_length_before_anything_is_sent():
    ops, _, _ = make(script=[thread(("user", "a"), ("assistant", "b"), count=12)])
    out = run(ops, "relay.snapshot")
    assert out.ok and out.data["snapshot"]["turn_count"] == 12
    assert out.data["snapshot"]["last_assistant"] == "b"


def test_settle_refuses_a_way_of_waiting_that_does_not_exist_and_lists_the_ones_that_do():
    ops, _, _ = make(script=ANSWERED)
    out = run(ops, "relay.settle", settle="whenever")
    assert not out.ok and "stable_text" in out.line


def test_settle_reports_a_reply_that_never_came_as_sent_but_unanswered():
    ops, _, _ = make(script=[thread(("user", "q"))])
    out = run(ops, "relay.settle", settle="new_turn", timeout=2)
    assert not out.ok and "The message was sent" in out.line and "relay.read" in out.line
    assert out.data["reply"] == ""


def test_answer_reports_a_partial_reply_as_partial():
    ops, _, _ = make()
    out = run(ops, "relay.answer", reply="Kent Coun", settled=False, instance="bati")
    assert out.ok and "still writing" in out.line
    done = run(ops, "relay.answer", reply="Kent County", settled=True, instance="bati")
    assert done.ok and "bati says" in done.line


def test_answer_with_nothing_to_report_refuses_rather_than_inventing_a_blank_reply():
    ops, _, _ = make()
    assert not run(ops, "relay.answer").ok


# -- the Composite ---------------------------------------------------------------
def test_ask_runs_the_seven_steps_in_order_and_reports_the_reply():
    ops, transport, _ = make(script=ANSWERED)
    out = run(ops, "relay.ask", message="what county?", settle="stable_text", timeout=60, stable_seconds=1)
    assert out.ok and "Kent County" in out.line
    assert [name for name, _ in ops["relay_ask"].trail] == ["relay_ready", "relay_guard", "relay_snapshot",
                                                            "relay_type", "relay_send", "relay_settle",
                                                            "relay_answer"]
    assert transport.calls.index("type") < transport.calls.index("send")


def test_ask_snapshots_before_it_types_so_an_old_answer_is_never_reported_as_the_new_one():
    """The thread already holds the perfect answer and nothing new arrives. The ask must fail, not
    hand back the history."""
    history = thread(("user", "what county?"), ("assistant", "Kent County"))
    ops, _, _ = make(script=[history])
    out = run(ops, "relay.ask", message="what county?", settle="new_turn", timeout=2)
    assert not out.ok and "nothing new came back" in out.line


def test_a_refused_ask_stops_at_the_step_that_refused_and_nothing_after_it_runs():
    ops, transport, _ = make(script=ANSWERED)
    out = run(ops, "relay.ask", message="the password is hunter2")
    assert not out.ok and out.data["guard"] == "credential"
    assert [name for name, _ in ops["relay_ask"].trail] == ["relay_ready", "relay_guard"]
    assert transport.typed == [] and "send" not in transport.calls


def test_an_unreachable_session_is_caught_by_the_ready_step_before_anything_is_typed():
    ops, transport, _ = make(script=ANSWERED, unreachable={"health"})
    out = run(ops, "relay.ask", message="hello")
    assert not out.ok and [name for name, _ in ops["relay_ask"].trail] == ["relay_ready"]
    assert transport.typed == []


def test_no_wait_delivers_the_message_and_ends_without_waiting_for_a_reply():
    """What a session-to-session message wants: waiting out the other instance's answer inside this run
    is how two relays would hold each other open."""
    ops, transport, _ = make(script=ANSWERED)
    out = run(ops, "relay.ask", message="over to you", no_wait=True)
    assert out.ok and "has not been read yet" in out.line
    assert [name for name, _ in ops["relay_ask"].trail] == ["relay_ready", "relay_guard", "relay_snapshot",
                                                            "relay_type", "relay_send"]


def test_a_relayed_message_that_has_already_crossed_a_relay_stops_here():
    ops, transport, _ = make(script=ANSWERED)
    out = run(ops, "relay.ask", message="keep passing this along", hops=3)
    assert not out.ok and out.data["guard"] == "loop"
    assert transport.typed == []


def test_each_step_can_be_run_as_its_own_phase_and_its_data_reaches_the_next_one():
    """What the ADW harness does: every step is a lifecycle phase of its own."""
    ops, _, _ = make(script=ANSWERED)
    seen: list[str] = []

    def as_phase(step, step_args):
        seen.append(step.name)
        return step.run(step_args)

    out = ops["relay_ask"].run({"message": "what county?", "settle": "stable_text", "timeout": 60,
                                "stable_seconds": 1}, step_runner=as_phase)
    assert out.ok and len(seen) == 7
    assert out.data["snapshot"]["turn_count"] == 1          # the Memento travelled to the settle step
    assert out.data["reply"] == "Kent County"


def test_an_unknown_instance_name_raises_for_the_catalog_and_is_a_refusal_for_an_operation():
    with pytest.raises(InstanceUnknown):
        StaticInstanceCatalog().resolve("martian")
    ops, _, _ = make()
    assert not run(ops, "relay.ask", message="hi", instance="martian").ok
