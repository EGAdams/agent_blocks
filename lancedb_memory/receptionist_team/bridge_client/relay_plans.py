"""One Plan per ChatGPT relay operation: the model's filled form -> the exact JSON prompt, and the sentence read back.

Strategy + Registry, the same shape as `browser_plans.py` and `notes_plans.py`. `ChatGptBuilder`
(specialists.py) finds the Plan for `form.operation` in `RELAY_PLANS` and asks it to `build()`; adding an
operation is one class here and one line in `RELAY_PLANS`. The Plans mirror the worker's table
(`agent_bridge_worker/chatgpt_relay_agent.py`) and the relay ADW (`adws/chatgpt_relay/`), both of which
re-check everything on their side -- this is Toyota's half of the loop ChatGPT already drives, so she reaches
the same ChatGPT session through the same Bridge rather than a second mechanism.

What code decides, never the model:

- the operation's lane (`inspect` or `implement`), so a message can never be sent as a read;
- `confirm` is never set. `new_chat` abandons the thread on screen, and the refusal that comes back is the
  right answer: EG says yes to that one himself, in the window, and a spoken yes to "shall I ask ChatGPT"
  is not a yes to discarding a conversation.
- the message is checked here before it is sent, not after. A password, a PIN or a one-time code typed into
  that window lands in a real account's permanent history, where no later refusal can reach it.

`ask` is the one to use. The rest exist because when an ask fails the next move depends on which half failed:
`send` presses a message that was already typed (retyping would post it twice), and `read` collects a reply
that was still being written when the wait ran out.
"""
import re
from typing import Protocol

from .browser_plans import CREDENTIAL, URL, words

# A session's name, as `instances` reports it: a short label, never a sentence and never an address.
INSTANCE = re.compile(r"^[A-Za-z0-9._-]{1,40}$")
SETTLE = ("stable_text", "new_turn", "none")
MAX_MESSAGE = 7000
MAX_HOPS = 4
MAX_LAST = 100


class IRelayPlan(Protocol):
    name: str
    operation: str      # the Bridge lane: "inspect" or "implement"

    def build(self, form, problem) -> tuple[dict, str]:
        """(prompt dict, read-back sentence). `problem(spoken)` raises the specialist's FormProblem."""
        ...


def _instance(form, problem) -> dict:
    """Which session. Omitted means the relay's own default, which is the common case."""
    name = str(form.instance or "").strip()
    if not name:
        return {}
    if not INSTANCE.fullmatch(name):
        problem("That doesn't look like one of my ChatGPT sessions. Ask me which sessions I can reach, "
                "then tell me which one.")
    return {"instance": name}


def _named(where: dict) -> str:
    return where.get("instance", "ChatGPT")


def _message(form, problem) -> str:
    text = form.message if form.message is not None else form.text
    text = str(text or "").strip()
    if not text:
        problem("What should I say to ChatGPT?")
    if len(text) > MAX_MESSAGE:
        problem(f"That's too long for one message; keep it under {MAX_MESSAGE} characters.")
    if CREDENTIAL.search(text):
        problem("That looks like a password or a code, and I never put one in a message. It would be saved in "
                "that account's history for good. Type it yourself in the window that's already open.")
    return text


class Sessions:
    """Where a message to another session starts: which sessions exist, and which is the default."""

    name, operation = "instances", "inspect"

    def build(self, form, problem):
        return {"relay": "instances"}, ""


class Status:
    name, operation = "health", "inspect"

    def build(self, form, problem):
        return {"relay": "health", **_instance(form, problem)}, ""


class Ready:
    name, operation = "ready", "inspect"

    def build(self, form, problem):
        return {"relay": "ready", **_instance(form, problem)}, ""


class Read:
    """Collect the thread. This is how a reply that was still being written is read once it is finished."""

    name, operation = "read", "inspect"

    def build(self, form, problem):
        req: dict = {"relay": "read", **_instance(form, problem)}
        if form.last is not None:
            last = int(form.last)
            if not 1 <= last <= MAX_LAST:
                problem(f"I can read 1 to {MAX_LAST} turns at a time.")
            req["last"] = last
        return req, ""


class Open:
    name, operation = "open", "implement"

    def build(self, form, problem):
        where = _instance(form, problem)
        url = str(form.url or "").strip()
        if url and not URL.fullmatch(url):
            problem("That doesn't look like a web address. Give me one starting with https://.")
        req: dict = {"relay": "open", **where}
        if url:
            req["url"] = url
        return req, (f"I'll start {_named(where)}'s browser. It's already signed in -- I never type a password.")


class Ask:
    """Send one message and wait for the reply to finish. The one to use."""

    name, operation = "ask", "implement"

    def build(self, form, problem):
        where = _instance(form, problem)
        message = _message(form, problem)
        req: dict = {"relay": "ask", **where, "message": message}
        settle = str(form.settle or "").strip().lower()
        if settle:
            if settle not in SETTLE:
                problem("I can wait for the reply to stop changing, for a new turn to appear, or not wait at all.")
            req["settle"] = settle
        if form.hops is not None:
            hops = int(form.hops)
            if not 0 <= hops <= MAX_HOPS:
                problem(f"That message has crossed too many relays already ({MAX_HOPS} is the limit).")
            req["hops"] = hops
        origin = str(form.origin or "").strip()
        if origin:
            if not INSTANCE.fullmatch(origin):
                problem("That doesn't look like one of my ChatGPT sessions.")
            req["origin"] = origin
        if form.no_wait is True:
            req["no_wait"] = True
            return req, f'I\'ll pass "{words(message, 80)}" to {_named(where)} and not wait for an answer.'
        return req, f'I\'ll ask {_named(where)}: "{words(message, 80)}", then read the reply back once it finishes.'


class Type:
    """Put a message in the box and stop, so EG can read it before it goes."""

    name, operation = "type", "implement"

    def build(self, form, problem):
        where = _instance(form, problem)
        message = _message(form, problem)
        return ({"relay": "type", **where, "message": message},
                f'I\'ll put "{words(message, 80)}" in {_named(where)}\'s box and leave it there. '
                "Nothing is sent until you say so.")


class Send:
    """Press send on what is already typed. Never retypes -- that would post the message twice."""

    name, operation = "send", "implement"

    def build(self, form, problem):
        where = _instance(form, problem)
        return ({"relay": "send", **where},
                f"I'll press send on what's already in {_named(where)}'s box, without retyping it. "
                "I won't wait for the reply; ask me to read the thread for that.")


class NewChat:
    """Refused on purpose: `confirm` is never set here. See this module's docstring."""

    name, operation = "new_chat", "implement"

    def build(self, form, problem):
        problem("Starting a new chat throws away the thread that's on screen, and I won't do that from here. "
                "Press the new chat button yourself in the window, then ask me again.")
        return {}, ""


RELAY_PLANS: dict[str, IRelayPlan] = {p.name: p for p in (
    Sessions(), Status(), Ready(), Read(),
    Ask(), Type(), Send(), Open(), NewChat(),
)}
