"""Chain of Responsibility: every reason a message may not be sent, one link each.

`GuardChain.check` asks each link in order and stops at the first refusal, so the reason a caller gets is
the most important one rather than a list. Adding a reason is one class plus one entry in `default_guards`;
no operation changes.

The links exist because the relay types into **somebody's real, signed-in ChatGPT account**. What goes in
is attributable to EG, it persists in that account's history and its cross-chat memory, and it can be read
by anyone with the window. That is a higher bar than an ordinary HTTP call, and these are the four parts
of it that are worth refusing in code rather than in a prompt.
"""
from __future__ import annotations

import re
from typing import Any

from .interfaces import IMessageGuard
from .models import GuardVerdict, RelayInstance

MAX_MESSAGE = 12000
MAX_HOPS = 1

#: What must never be typed into a page, whatever the caller calls it. Same list as the browser relay's
#: `browser_plans.CREDENTIAL`, kept separate on purpose: these two refusals are allowed to diverge.
# The suffix is required, not optional: `pass` on its own matches "passing this along", and a relay that
# refuses an ordinary sentence as a credential teaches a caller to stop believing the refusals.
# "PIN" is spelled out or it is next to a number; a bare lowercase "pin" is "pin the tab".
_PIN = r"(?-i:\bPIN\b)|\bpin\s*(code|number|#)|\bpin\b(?=\D{0,8}\d)"
CREDENTIAL = re.compile(rf"pass(word|wd|code)s?|{_PIN}|\botp\b|one.?time.?(code|pass)|mfa|2fa|totp|secret|"
                        r"\btokens?\b|api.?key|\bcvv\b|\bcvc\b|\bssn\b|private.?key|BEGIN [A-Z ]*PRIVATE KEY",
                        re.IGNORECASE)


class NonEmptyGuard:
    """A message with nothing in it is a mistake, not a message -- and /type with an empty string leaves
    the prompt box untouched, so /send would re-submit whatever was already sitting in it."""

    name = "non_empty"

    def check(self, message: str, args: dict[str, Any], instance: RelayInstance) -> GuardVerdict:
        if not message.strip():
            return GuardVerdict.refuse(self.name, "there is no message to send. Nothing was typed.")
        return GuardVerdict.allow()


class LengthGuard:
    """Long enough to be a problem is long enough to refuse. The prompt box takes it, but a 100KB paste
    typed key-by-key through WebDriver takes minutes and looks exactly like a hang."""

    name = "length"

    def __init__(self, limit: int = MAX_MESSAGE) -> None:
        self._limit = limit

    def check(self, message: str, args: dict[str, Any], instance: RelayInstance) -> GuardVerdict:
        if len(message) > self._limit:
            return GuardVerdict.refuse(self.name, f"that message is {len(message)} characters; the relay sends "
                                                  f"up to {self._limit}. Send the part that matters.")
        return GuardVerdict.allow()


class CredentialGuard:
    """No password, PIN, one-time code, API key or private key is ever typed into the page.

    This is not about trusting the caller. It is that the message lands in a real account's permanent
    history, so a secret sent here is a secret published -- and the relay has no way to unsend it.
    """

    name = "credential"

    def check(self, message: str, args: dict[str, Any], instance: RelayInstance) -> GuardVerdict:
        if CREDENTIAL.search(message):
            return GuardVerdict.refuse(self.name,
                                       "that looks like a password, a code or a key, and the relay never types one "
                                       "into the page -- it would land in the account's permanent history. "
                                       "Say where it is kept (the variable name, the file), never its value.")
        return GuardVerdict.allow()


class LoopGuard:
    """Two instances that can message each other can message each other forever.

    `--hops` counts how many relays a message has already crossed. A caller that is itself answering a
    relayed message passes the hop count it received; the relay refuses past `MAX_HOPS` unless the
    *origin* instance is marked `trusted_sender` in the registry. Nothing is trusted today, so the limit
    is one hop and a ping-pong cannot start. This is the one guard that exists because of the specific
    request "ChatGPT may want to send a message to another instance of itself".
    """

    name = "loop"

    def __init__(self, max_hops: int = MAX_HOPS) -> None:
        self._max = max_hops

    def check(self, message: str, args: dict[str, Any], instance: RelayInstance) -> GuardVerdict:
        try:
            hops = int(args.get("hops") or 0)
        except (TypeError, ValueError):
            return GuardVerdict.refuse(self.name, "--hops must be a number: how many relays this message has crossed.")
        if hops < 0:
            return GuardVerdict.refuse(self.name, "--hops cannot be negative.")
        if hops > self._max and not instance.trusted_sender:
            return GuardVerdict.refuse(self.name,
                                       f"this message has already crossed {hops} relays and {instance.label} is not a "
                                       f"trusted origin, so it stops here. Two instances relaying to each other "
                                       f"would not stop on their own. A person can carry it the next hop.")
        if hops == self._max:
            return GuardVerdict.allow()
        return GuardVerdict.allow()


class SelfTalkGuard:
    """A relay must not deliver a message to the instance it came from -- that is a conversation with
    oneself through two HTTP calls, and it is how a loop looks on one machine."""

    name = "self_talk"

    def check(self, message: str, args: dict[str, Any], instance: RelayInstance) -> GuardVerdict:
        origin = str(args.get("origin") or "").strip().lower()
        if origin and origin == instance.label.lower():
            return GuardVerdict.refuse(self.name, f"the message came from {instance.label} and would be delivered back "
                                                  f"to {instance.label}. Name a different instance.")
        return GuardVerdict.allow()


class GuardChain:
    """The Chain itself, and an IMessageGuard in its own right -- so a chain can hold a chain."""

    name = "chain"

    def __init__(self, links: list[IMessageGuard]) -> None:
        self._links = list(links)

    @property
    def links(self) -> list[IMessageGuard]:
        return list(self._links)

    def check(self, message: str, args: dict[str, Any], instance: RelayInstance) -> GuardVerdict:
        for link in self._links:
            verdict = link.check(message, args, instance)
            if not verdict.allowed:
                return verdict
        return GuardVerdict.allow()


def default_guards() -> GuardChain:
    """Order is the behaviour: an empty message is reported as empty rather than as too short, and a
    credential is refused before anything counts hops -- the most specific, most consequential refusal
    the caller needs to hear first."""
    return GuardChain([NonEmptyGuard(), CredentialGuard(), LengthGuard(), SelfTalkGuard(), LoopGuard()])
