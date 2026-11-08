"""Strategy: how we decide ChatGPT has finished answering.

ChatGPT streams its reply a token at a time, so one read after /send is never the answer -- it is the
first few words of it. Every policy here is the same loop (read the thread, look, wait, read again) with
a different stopping rule, which is exactly what a Strategy is for: the operation asks for a settle and
does not care which rule applied.

    StableTextSettle   the newest assistant turn stopped changing for `stable_seconds`   (the default)
    NewTurnSettle      a new assistant turn appeared at all                              (debugging)
    NoSettle           read once and report what is there                               (Null Object)

Two facts shape all three. A reply is "new" only against a `ThreadSnapshot` taken before we typed --
ChatGPT's cross-chat memory means an old turn can read like a fresh answer. And `settled=False` is never
reported as a complete answer: the deadline winning is a partial reply, and the caller is told so.
"""
from __future__ import annotations

from .interfaces import IRelayClock, IRelayObserver, IRelayTransport
from .models import RelayInstance, SettleReport, ThreadSnapshot, ThreadView

DEFAULT_TIMEOUT = 120.0
DEFAULT_STABLE = 3.0
DEFAULT_POLL = 2.0
READ_TURNS = 6          # the last few turns: enough to see the question and the answer, not the history


class StableTextSettle:
    """The newest assistant turn has been the same text for `stable_seconds`, and it arrived after the
    snapshot. That is the only signal available without reading ChatGPT's internals: there is no "done"
    flag in the DOM we are willing to depend on, and the text going quiet is what a person watches for.

    `stable_seconds` is a trade, not a tuning knob: too short and a pause mid-sentence reads as finished,
    too long and every answer costs that much extra. Three seconds is the browser server's own default
    for /get_response, measured against the same UI.
    """

    name = "stable_text"

    def __init__(self, clock: IRelayClock, timeout: float = DEFAULT_TIMEOUT,
                 stable_seconds: float = DEFAULT_STABLE, poll_seconds: float = DEFAULT_POLL) -> None:
        self._clock = clock
        self._timeout = max(1.0, float(timeout))
        self._stable = max(0.0, float(stable_seconds))
        self._poll = max(0.25, float(poll_seconds))

    def settle(self, instance: RelayInstance, transport: IRelayTransport, before: ThreadSnapshot,
               observer: IRelayObserver) -> SettleReport:
        started = self._clock.monotonic()
        deadline = started + self._timeout
        polls = 0
        seen_text = ""
        quiet_since: float | None = None
        thread = ThreadView()

        while True:
            thread = transport.read_thread(instance, READ_TURNS)
            polls += 1
            now = self._clock.monotonic()
            newest = thread.last_assistant
            fresh = before.is_new(newest, thread)
            text = newest.text if (newest and fresh) else ""

            if text and text == seen_text:
                if quiet_since is None:
                    quiet_since = now
                if now - quiet_since >= self._stable:
                    report = SettleReport(settled=True, polls=polls, waited=round(now - started, 2),
                                          why=f"the reply stopped changing for {self._stable:g}s", thread=thread)
                    observer.settled(report)
                    return report
            else:
                quiet_since = None
                seen_text = text

            if now >= deadline:
                why = (f"still being written after {self._timeout:g}s" if text
                       else f"no new reply after {self._timeout:g}s")
                report = SettleReport(settled=False, polls=polls, waited=round(now - started, 2),
                                      why=why, thread=thread)
                observer.settled(report)
                return report

            observer.waiting(polls, round(now - started, 2),
                             f"{len(text)} chars so far" if text else "no new reply yet")
            self._clock.sleep(self._poll)


class NewTurnSettle:
    """Stop the moment a new assistant turn exists, finished or not. For debugging the relay itself --
    it answers "did the message arrive" without waiting out a long reply, and it is honest about it:
    `settled` is True for the arrival, and the `why` line says the reply may still be growing."""

    name = "new_turn"

    def __init__(self, clock: IRelayClock, timeout: float = 30.0, poll_seconds: float = 1.0) -> None:
        self._clock = clock
        self._timeout = max(1.0, float(timeout))
        self._poll = max(0.25, float(poll_seconds))

    def settle(self, instance: RelayInstance, transport: IRelayTransport, before: ThreadSnapshot,
               observer: IRelayObserver) -> SettleReport:
        started = self._clock.monotonic()
        polls = 0
        thread = ThreadView()
        while True:
            thread = transport.read_thread(instance, READ_TURNS)
            polls += 1
            now = self._clock.monotonic()
            if before.is_new(thread.last_assistant, thread):
                report = SettleReport(settled=True, polls=polls, waited=round(now - started, 2),
                                      why="a new reply appeared; it may still be being written", thread=thread)
                observer.settled(report)
                return report
            if now - started >= self._timeout:
                report = SettleReport(settled=False, polls=polls, waited=round(now - started, 2),
                                      why=f"no new reply after {self._timeout:g}s", thread=thread)
                observer.settled(report)
                return report
            observer.waiting(polls, round(now - started, 2), "no new reply yet")
            self._clock.sleep(self._poll)


class NoSettle:
    """Null Object: read once, report it, wait for nothing. What `relay.read` uses, and what a test uses
    when the waiting is not the thing under test."""

    name = "none"

    def settle(self, instance: RelayInstance, transport: IRelayTransport, before: ThreadSnapshot,
               observer: IRelayObserver) -> SettleReport:
        thread = transport.read_thread(instance, READ_TURNS)
        report = SettleReport(settled=True, polls=1, waited=0.0, why="read once, waited for nothing",
                              thread=thread)
        observer.settled(report)
        return report


POLICIES = {policy.name: policy for policy in (StableTextSettle, NewTurnSettle, NoSettle)}
