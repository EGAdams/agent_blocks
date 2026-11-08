"""The settle Strategies: when a streaming reply counts as finished, and when it counts as partial."""
from __future__ import annotations

from chatgpt_relay.models import RelayInstance, ThreadSnapshot
from chatgpt_relay.observers import RecordingObserver
from chatgpt_relay.settling import NewTurnSettle, NoSettle, StableTextSettle
from chatgpt_relay.transport import HttpRelayTransport

from .fakes import FakeClock, FakeTransport, thread

BATI = RelayInstance(label="bati", base_url="http://x")


def run(policy, script, before=None):
    transport = FakeTransport(script=script)
    observer = RecordingObserver()
    report = policy.settle(BATI, transport, before or ThreadSnapshot(), observer)
    return report, transport, observer


def test_a_reply_that_stopped_growing_is_settled_and_the_whole_text_is_reported():
    clock = FakeClock()
    script = [thread(("user", "q"), ("assistant", "Ken")),
              thread(("user", "q"), ("assistant", "Kent Coun")),
              thread(("user", "q"), ("assistant", "Kent County")),
              thread(("user", "q"), ("assistant", "Kent County"))]
    report, _, observer = run(StableTextSettle(clock, timeout=60, stable_seconds=2, poll_seconds=1), script)
    assert report.settled and report.thread.last_assistant.text == "Kent County"
    assert "stopped changing" in report.why
    assert observer.reports and observer.notes


def test_the_deadline_winning_mid_sentence_is_reported_as_still_being_written_never_as_an_answer():
    clock = FakeClock()
    script = [thread(("user", "q"), ("assistant", "one")), thread(("user", "q"), ("assistant", "one two")),
              thread(("user", "q"), ("assistant", "one two three"))]
    report, _, _ = run(StableTextSettle(clock, timeout=3, stable_seconds=5, poll_seconds=1), script)
    assert not report.settled and "still being written" in report.why


def test_nothing_coming_back_at_all_says_no_new_reply_and_not_that_it_is_still_writing():
    clock = FakeClock()
    report, _, _ = run(StableTextSettle(clock, timeout=2, stable_seconds=1, poll_seconds=1),
                       [thread(("user", "q"))])
    assert not report.settled and "no new reply" in report.why


def test_an_old_assistant_turn_is_not_mistaken_for_a_fresh_answer():
    """The Memento is the whole point: ChatGPT remembers across chats, so a turn that answers the
    question may predate the question."""
    clock = FakeClock()
    history = thread(("user", "q"), ("assistant", "Kent County"))
    before = ThreadSnapshot.of(HttpRelayTransport.to_thread(history))
    report, _, _ = run(StableTextSettle(clock, timeout=2, stable_seconds=1, poll_seconds=1),
                       [history, history, history], before=before)
    assert not report.settled and "no new reply" in report.why


def test_a_longer_version_of_the_same_old_answer_does_count_as_new():
    clock = FakeClock()
    before = ThreadSnapshot.of(HttpRelayTransport.to_thread(thread(("assistant", "Kent"))))
    grown = thread(("assistant", "Kent County, recorded 1874"))
    report, _, _ = run(StableTextSettle(clock, timeout=10, stable_seconds=1, poll_seconds=1),
                       [grown, grown], before=before)
    assert report.settled and report.thread.last_assistant.text.startswith("Kent County")


def test_new_turn_settling_stops_at_the_first_sign_of_life_and_says_the_reply_may_still_grow():
    clock = FakeClock()
    report, transport, _ = run(NewTurnSettle(clock, timeout=10, poll_seconds=1),
                               [thread(("user", "q")), thread(("user", "q"), ("assistant", "Ke"))])
    assert report.settled and "may still be being written" in report.why
    assert transport.reads == 2


def test_no_settling_reads_exactly_once_and_waits_for_nothing():
    report, transport, _ = run(NoSettle(), [thread(("assistant", "whatever"))])
    assert report.settled and report.polls == 1 and transport.reads == 1
