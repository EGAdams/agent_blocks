"""The relay's operations. Each one is a Command -- constructed with the RelayPlane, run with a dict of
arguments, answering an `OperationOutcome` (ok, one line a caller can repeat, structured data).

    relay.instances   which ChatGPT sessions this relay knows            (no network at all)
    relay.health      is one of them reachable, and is Chrome up
    relay.ready       is one of them in a state that can be typed into
    relay.open        point that Chrome at a page -- the only operation that navigates
    relay.read        the last few turns of the thread as it stands
    relay.new_chat    start a fresh thread -- discards the current one
    relay.type        put text in the prompt box without sending it
    relay.send        press send on whatever is in the prompt box
    relay.snapshot    remember the thread before we add to it            (the Memento step)
    relay.guard       may this message be sent at all                    (the Chain of Responsibility step)
    relay.settle      wait for the reply to stop being written           (the Strategy step)
    relay.answer      report the reply that arrived                      (presentation only)
    relay.ask         all of the above, in order                         (the Composite -- the one to use)

`relay.ask` is deliberately assembled out of seven traced steps rather than written as one function.
It costs nothing at runtime and it buys the thing this relay has always lacked: when an ask fails, the
lifecycle log says *which* step failed -- the message never arrived, or it arrived and nothing came back,
or something came back and we stopped waiting too early. Those have three different next moves.

There is no quit. See `interfaces.IRelayTransport`.
"""
from __future__ import annotations

from typing import Any

from agent_comms.operations.base import OperationOutcome

from .errors import RelayError, RelayRefused, RelayUnreachable
from .instances import InstanceUnknown
from .models import RelayInstance, SettleReport, ThreadSnapshot, ThreadView
from .plane import RelayPlane

CHATGPT_URL = "https://chatgpt.com/"
MAX_REPLY = 20000
MAX_SHOWN = 400
READ_TURNS = 20


def ok(line: str, **data: Any) -> OperationOutcome:
    return OperationOutcome(ok=True, line=line, data=data)


def refused(line: str, **data: Any) -> OperationOutcome:
    return OperationOutcome(ok=False, line=line, data=data)


def trouble(body: dict[str, Any]) -> str:
    """The server's own report of a failure, or "" when it did not fail.

    Each endpoint answers in its own vocabulary -- /health says "ok", /open and /new_chat say "opened",
    /type says "typed", /send says "sent" -- so a success cannot be one magic word. A failure, though, is
    always `{"status": "error", "error": "...", "details": "..."}`, and that is the thing worth matching.
    Reading it the other way around is a real mistake and this relay made it: "typed" was reported to the
    operator as "did not take the text" on its first live run, with the server's own body right there in
    the line saying it had.
    """
    if str(body.get("status") or "").lower() == "error" or body.get("error"):
        return short(f"{body.get('error') or 'error'}: {body.get('details') or body}", 200)
    return ""


def short(text: str, limit: int = MAX_SHOWN) -> str:
    one = " ".join((text or "").split())
    return one if len(one) <= limit else one[: limit - 1] + "…"


class RelayOperation:
    """Template Method: `run` turns any RelayError into a refusal, `perform` is the subclass's.

    The relay's own `Operation` base rather than browser_workflows' one, because the errors it catches are
    this package's -- and because an operation here must not have to import a browser package to exist.
    The shape is IOperation's, so the ADW harness, `OperationSequence` and the post office all take it.
    """

    name = ""
    description = ""
    performer = "chatgpt_relay"
    redact = ("message",)      # somebody's message is never written to the lifecycle log

    def __init__(self, plane: RelayPlane) -> None:
        self._plane = plane

    def run(self, args: dict[str, Any]) -> OperationOutcome:
        try:
            return self.perform(args)
        except (RelayError, InstanceUnknown) as exc:
            return refused(str(exc))

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        raise NotImplementedError

    # -- shared plumbing ---------------------------------------------------------
    def _instance(self, args: dict[str, Any]) -> RelayInstance:
        return self._plane.catalog.resolve(args.get("instance"))

    def _message(self, args: dict[str, Any]) -> str:
        text = args.get("message")
        if text is None:
            raise RelayRefused("say what to send: --message.")
        return str(text)

    def _snapshot(self, args: dict[str, Any]) -> ThreadSnapshot:
        """The Memento as it travels through the sequence's data, or an empty one for a direct call."""
        carried = args.get("snapshot")
        if isinstance(carried, dict):
            return ThreadSnapshot.model_validate(carried)
        return ThreadSnapshot()

    def _turns(self, args: dict[str, Any]) -> int:
        given = args.get("last")
        try:
            last = READ_TURNS if given in (None, "") else int(given)
        except (TypeError, ValueError):
            raise RelayRefused("--last takes a number of turns.")
        if not 1 <= last <= 100:
            raise RelayRefused("--last takes 1 to 100 turns.")
        return last


class Instances(RelayOperation):
    """Which sessions exist. No network, so it answers even when every one of them is asleep -- which is
    what makes it the right first call when a relay is failing and you are not sure what you are naming."""

    name = "relay_instances"
    description = "relay.instances: the ChatGPT sessions this relay can reach, and who owns each"

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        found = self._plane.catalog.all()
        lines = [f"{i.label}: {i.base_url} -- {i.description or 'no description'} (owner {i.owner or 'unknown'}"
                 f"{', trusted sender' if i.trusted_sender else ''})" for i in found]
        return ok(f"{len(found)} ChatGPT session{'' if len(found) == 1 else 's'}:\n" + "\n".join(lines),
                  instances=[i.model_dump() for i in found])


class Health(RelayOperation):
    name = "relay_health"
    description = "relay.health: is one session's browser server answering, and is its Chrome up"

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        instance = self._instance(args)
        body = self._plane.transport.health(instance)
        started = body.get("browser_started")
        if trouble(body):
            return refused(f"{instance.label} answered, but not well: {trouble(body)}", health=body)
        where = "its Chrome is up" if started else "its Chrome is not started yet (the first call starts it)"
        return ok(f"{instance.label} is reachable at {instance.base_url} and {where}.",
                  instance=instance.label, health=body, browser_started=bool(started))


class Ready(RelayOperation):
    """Is this session in a state that can be typed into -- and if not, what to run.

    Separate from `relay.health` on purpose. Health is the diagnostic a person runs when a relay is
    failing, and it answers even when the browser is down, because "reachable but Chrome is not up" is a
    useful answer. Ready is the gate `relay.ask` opens with, and for it that same state is a refusal:
    typing into a Chrome that has not started gets `prompt_not_found`, which reads like a broken relay.

    It refuses rather than opening the page itself. Opening chatgpt.com navigates the window, and that
    abandons whatever thread the person had on screen -- the relay does not get to make that choice.
    """

    name = "relay_ready"
    description = "relay.ready: is this session ready to be typed into (and if not, what to run)"

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        instance = self._instance(args)
        body = self._plane.transport.health(instance)
        if trouble(body):
            return refused(f"{instance.label} answered, but not well: {trouble(body)}", health=body)
        if not body.get("browser_started"):
            return refused(f"{instance.label} is reachable, but its Chrome has not started, so there is no "
                           f"prompt box to type into. Run relay.open first -- it will land on a new "
                           f"ChatGPT thread, which is a choice somebody should make deliberately.",
                           instance=instance.label, health=body, browser_started=False)
        return ok(f"{instance.label} is ready.", instance=instance.label, health=body, browser_started=True)


class Open(RelayOperation):
    """Point that Chrome at a page. The only operation here that navigates, and it says what that costs.

    Landing on chatgpt.com's front door is a new, empty thread; landing on a /c/<id> link is that
    conversation. Either way whatever was on screen is left behind, which is why this is its own
    operation and never a silent step inside `relay.ask`.
    """

    name = "relay_open"
    description = "relay.open: point one session's Chrome at a page (this leaves the current page behind)"

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        instance = self._instance(args)
        url = str(args.get("url") or CHATGPT_URL).strip()
        if not url.lower().startswith(("http://", "https://")):
            raise RelayRefused(f"{url!r} is not a web address. Give one starting with https://.")
        body = self._plane.transport.open(instance, url)
        if trouble(body):
            return refused(f"{instance.label} did not open {url}: {trouble(body)}", result=body)
        return ok(f"{instance.label}'s Chrome is on {url}. Whatever was on screen before is left behind.",
                  instance=instance.label, url=url, result=body)


class Read(RelayOperation):
    """The thread as it stands. Read-only and it changes nothing, so it is the safe way to find out what
    happened after a send that looked like it failed."""

    name = "relay_read"
    description = "relay.read: the last few turns of one session's current ChatGPT thread"

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        instance = self._instance(args)
        thread = self._plane.transport.read_thread(instance, self._turns(args))
        if not thread.turns:
            return ok(f"{instance.label}'s thread reads as empty. If a reply was expected, the page may be "
                      f"on something other than a conversation -- relay.health first.",
                      instance=instance.label, turn_count=thread.turn_count, turns=[])
        shown = "\n".join(f"{t.role}: {short(t.text)}" for t in thread.turns)
        return ok(f"{instance.label}, {thread.turn_count} turns in the thread; the last "
                  f"{len(thread.turns)}:\n{shown}",
                  instance=instance.label, turn_count=thread.turn_count,
                  turns=[t.model_dump() for t in thread.turns])


class NewChat(RelayOperation):
    """Start a fresh thread. **This discards the current one's context** -- the golden rule in the relay
    skill is not to do it silently, so it refuses without --confirm and says what is lost."""

    name = "relay_new_chat"
    description = "relay.new_chat: start a fresh ChatGPT thread (the current thread's context is left behind)"

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        instance = self._instance(args)
        if not args.get("confirm"):
            return refused(f"starting a new chat on {instance.label} leaves the current thread's context "
                           f"behind, and that thread is {instance.owner or 'a person'}'s real conversation. "
                           f"Re-run with --confirm if that is what is wanted. Nothing was changed.")
        body = self._plane.transport.new_chat(instance)
        if trouble(body):
            return refused(f"{instance.label} did not start a new chat: {trouble(body)}", result=body)
        return ok(f"{instance.label} is on a new, empty thread.", instance=instance.label, result=body)


class Type(RelayOperation):
    """Put the message in the prompt box and stop. Nothing is sent, which is why this is the operation to
    use when a person wants to read what the relay was about to say before it says it."""

    name = "relay_type"
    description = "relay.type: put a message in one session's prompt box without sending it"

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        instance = self._instance(args)
        message = self._message(args)
        verdict = self._plane.guards.check(message, args, instance)
        if not verdict.allowed:
            return refused(f"not typed: {verdict.reason}", guard=verdict.guard)
        try:
            body = self._plane.transport.type(instance, message)
        except RelayUnreachable as exc:
            # A timed-out /type is ambiguous: the text may be sitting in the prompt box, half typed. It is
            # never retried from here (that is how a message gets posted twice), so the caller is told
            # where to look rather than having it guessed for them.
            return refused(f"{exc} Nothing was sent, but some of the message may be sitting in "
                           f"{instance.label}'s prompt box -- relay.read, or clear it by hand.")
        if trouble(body):
            return refused(f"{instance.label} did not take the text: {trouble(body)}. Nothing was sent.",
                           result=body)
        return ok(f"{len(message)} characters are in {instance.label}'s prompt box, not sent.",
                  instance=instance.label, typed_chars=len(message), result=body)


class Send(RelayOperation):
    """Press send on whatever is in the prompt box. Never retried anywhere in this package: a send that
    in fact arrived would post the message twice into somebody's conversation."""

    name = "relay_send"
    description = "relay.send: press send on what is already in one session's prompt box"

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        instance = self._instance(args)
        body = self._plane.transport.send(instance)
        if trouble(body):
            return refused(f"{instance.label} did not send: {trouble(body)}", result=body)
        return ok(f"sent on {instance.label}. The reply is being written; it has not been read yet.",
                  instance=instance.label, sent=True, result=body)


class Snapshot(RelayOperation):
    """Memento: what the thread looked like before we added to it.

    This step is why a settled read can tell a fresh answer from history. ChatGPT carries memory across
    chats, so an assistant turn that answers the question perfectly may have been there before we asked.
    Without the snapshot the relay's commonest failure is silent and convincing.
    """

    name = "relay_snapshot"
    description = "relay.snapshot: remember the thread's state before sending, so the reply can be told apart"

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        instance = self._instance(args)
        thread = self._plane.transport.read_thread(instance, 2)
        before = ThreadSnapshot.of(thread)
        return ok(f"{instance.label} was at {before.turn_count} turns before sending.",
                  instance=instance.label, snapshot=before.model_dump())


class Guard(RelayOperation):
    """The Chain of Responsibility as its own step, so the trace records that the message was checked and
    which link had the last word -- not only that it went."""

    name = "relay_guard"
    description = "relay.guard: may this message be sent at all (empty, too long, a credential, a loop)"

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        instance = self._instance(args)
        message = self._message(args)
        verdict = self._plane.guards.check(message, args, instance)
        if not verdict.allowed:
            return refused(f"not sent: {verdict.reason}", guard=verdict.guard)
        return ok(f"{len(message)} characters, cleared to send to {instance.label}.",
                  instance=instance.label, message_chars=len(message))


class Settle(RelayOperation):
    """Wait for the reply to stop being written, by whichever Strategy `--settle` named.

    `settled=False` is reported as a partial answer and never as a finished one: the deadline winning
    means we stopped looking, not that ChatGPT stopped writing.
    """

    name = "relay_settle"
    description = "relay.settle: wait until the newest reply stops changing (or the deadline wins)"

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        instance = self._instance(args)
        policy = self._plane.settles.build(args.get("settle"), _number(args.get("timeout"), "--timeout"),
                                           _number(args.get("stable_seconds"), "--stable-seconds"))
        report = policy.settle(instance, self._plane.transport, self._snapshot(args), self._plane.observer)
        reply = _new_reply(report, self._snapshot(args))
        data = {"instance": instance.label, "settled": report.settled, "polls": report.polls,
                "waited": report.waited, "why": report.why, "reply": reply[:MAX_REPLY],
                "settle_policy": policy.name}
        if not reply:
            return refused(f"nothing new came back from {instance.label}: {report.why} "
                           f"({report.polls} reads over {report.waited:g}s). The message was sent; "
                           f"relay.read will show the thread as it stands.", **data)
        return ok(f"{instance.label} {'answered' if report.settled else 'is still answering'} after "
                  f"{report.waited:g}s.", **data)


class Answer(RelayOperation):
    """Presentation only: turn the settle step's data into the line a caller repeats.

    It is a separate step because reporting a *partial* answer as partial is a rule worth having one
    place for -- an operator reading "ChatGPT said X" needs to know whether X was finished.
    """

    name = "relay_answer"
    description = "relay.answer: report the reply the settle step found, finished or partial"

    def perform(self, args: dict[str, Any]) -> OperationOutcome:
        reply = str(args.get("reply") or "")
        if not reply:
            return refused("there is no reply to report. Run relay.settle, or relay.read to see the thread.")
        settled = bool(args.get("settled"))
        label = str(args.get("instance") or "the session")
        head = f"{label} says" if settled else f"{label} is still writing; so far it says"
        return ok(f"{head}:\n{reply[:MAX_REPLY]}", instance=label, reply=reply[:MAX_REPLY], settled=settled)


def _number(value: Any, flag: str) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise RelayRefused(f"{flag} takes a number of seconds.")


def _new_reply(report: SettleReport, before: ThreadSnapshot) -> str:
    newest = report.thread.last_assistant
    return newest.text if (newest and before.is_new(newest, report.thread)) else ""


ORDER = [Instances, Health, Ready, Open, Read, NewChat, Type, Send, Snapshot, Guard, Settle, Answer]
