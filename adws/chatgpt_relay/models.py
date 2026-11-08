"""The values that cross between the relay's pieces. Data only -- no behaviour, no imports from the package.

Pydantic because every one of these crosses a boundary: an operation's `data`, the ADW's `--json-out`,
the worker's `details`, and the gateway's `structuredContent` all carry them.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RelayInstance(BaseModel):
    """One reachable ChatGPT browser session, as data.

    `label` is how a caller names it ("bati"). `base_url` is the browser server in front of it. `owner` is
    whose logged-in account it is, which matters because a relayed message is attributable to a person, not
    to an agent. `trusted_sender` is whether this instance may be *sent to* by another relay message that
    itself arrived over a relay -- see guards.LoopGuard.
    """
    model_config = ConfigDict(frozen=True)

    label:          str
    base_url:       str
    owner:          str = ""
    description:    str = ""
    trusted_sender: bool = False


class RelayTurn(BaseModel):
    """One message in the thread, as the browser server reports it."""
    model_config = ConfigDict(frozen=True)

    role:    str                  # "user" or "assistant"
    turn_id: str = ""
    text:    str = ""


class ThreadView(BaseModel):
    """The thread as it stands. `turn_count` is the server's own count, not len(turns), because a `last=N`
    read returns fewer turns than the thread holds and the difference is the whole point of a snapshot."""
    turns:      list[RelayTurn] = Field(default_factory=list)
    turn_count: int = 0

    @property
    def last_assistant(self) -> RelayTurn | None:
        for turn in reversed(self.turns):
            if turn.role == "assistant":
                return turn
        return None


class ThreadSnapshot(BaseModel):
    """Memento: what the thread looked like before we sent anything, so the reply can be told from the history.

    Taken by `relay.ask` before typing and handed forward in the sequence's data. Without it a settled read
    cannot say which turns are new -- ChatGPT's cross-chat memory means an old assistant turn can look like
    a fresh answer to the question just asked.
    """
    model_config = ConfigDict(frozen=True)

    turn_count:    int = 0
    last_assistant: str = ""       # the text of the newest assistant turn at snapshot time

    @classmethod
    def of(cls, thread: ThreadView) -> "ThreadSnapshot":
        newest = thread.last_assistant
        return cls(turn_count=thread.turn_count, last_assistant=newest.text if newest else "")

    def is_new(self, turn: RelayTurn | None, thread: ThreadView) -> bool:
        """Is this assistant turn something that arrived after the snapshot?"""
        if turn is None or not turn.text:
            return False
        return thread.turn_count > self.turn_count or turn.text != self.last_assistant


class GuardVerdict(BaseModel):
    """Chain of Responsibility result: the first link that refuses explains why, and names itself."""
    model_config = ConfigDict(frozen=True)

    allowed: bool
    guard:   str = ""
    reason:  str = ""

    @classmethod
    def allow(cls) -> "GuardVerdict":
        return cls(allowed=True)

    @classmethod
    def refuse(cls, guard: str, reason: str) -> "GuardVerdict":
        return cls(allowed=False, guard=guard, reason=reason)


class SettleReport(BaseModel):
    """What the settle Strategy observed while waiting. `settled` False means the deadline won, which is
    reported as a partial answer and never as a complete one."""
    settled:  bool = False
    polls:    int = 0
    waited:   float = 0.0
    why:      str = ""
    thread:   ThreadView = Field(default_factory=ThreadView)
