"""Values that cross a boundary in the Machine Supervisor. Data only, no behaviour."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SshTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str
    port: int = 22
    user: str = "adamsl"


class Check(BaseModel):
    """One thing the cron watch looks at. `kind` picks the probe; nothing here runs a model."""
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    kind: Literal["http", "unit", "tcp", "command"]
    target: str = ""                    # http: url | unit: unit name | tcp: host:port
    argv: list[str] = Field(default_factory=list)   # command: exit 0 is healthy
    scope: Literal["user", "system"] = "user"       # unit only
    expect: int = 200                   # http only
    timeout: int = 10
    threshold: int = Field(default=2, ge=1)         # consecutive failures before the agent is woken
    about: str = ""                     # what it is and what usually fixes it, for the agent


class Machine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[A-Z0-9]+$")       # BATI, SQMC, R46
    hostname: str
    ssh: SshTarget
    always_on: bool = True              # False: being switched off is normal, not a failure
    python: str                         # interpreter with pydantic, by absolute path (cron and ssh have no PATH)
    repo: str
    claude: str
    peers: list[str] = Field(default_factory=list)  # who is woken next, in order
    checks: list[Check] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @property
    def agent(self) -> str:
        """The supervisor's mailbox name. No underscores: they separate handoff filename fields."""
        return f"supervisor-{self.name.lower()}"


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = "sonnet"
    agent_timeout: int = 900            # one repair attempt
    ask_timeout: int = 300              # one answer to a peer's question
    mail_timeout: int = 180
    cooldown_minutes: int = 360         # after an unresolved incident, before the ladder runs again
    settle_seconds: int = 10            # between an agent finishing and the recheck
    recheck_tries: int = 3
    max_wakes_per_day: int = 8          # a flapping check must not wake a model all day
    mail_retry_minutes: int = 30        # how often held mail is tried again
    todo_batch: int = 3                 # todos one worker run takes
    todo_senders: list[str] = Field(default_factory=lambda: ["chatgpt-bridge", "toyota-bridge", "sydney-bridge",
                                                             "operator"])


class CheckResult(BaseModel):
    check: str
    ok: bool
    detail: str = ""


class AgentReport(BaseModel):
    """What one supervisor says it found and did. Never the test of whether it worked: the recheck is."""
    machine: str
    role: Literal["first", "helper", "todo"]
    reached: bool = True                # False: the agent could not be woken at all
    diagnosis: str = ""
    actions: list[str] = Field(default_factory=list)
    believes_fixed: bool = False
    needs: str = ""                     # what only a person or a more capable agent can do
    cost_usd: float | None = None
    error: str = ""


class Attempt(BaseModel):
    rung: int
    machine: str
    report: AgentReport
    recheck: list[CheckResult]
    fixed: bool
    at: datetime


class Incident(BaseModel):
    id: str
    machine: str                        # the machine whose watch saw it and runs the ladder
    opened_at: datetime
    checks: list[Check]
    failures: list[CheckResult]
    attempts: list[Attempt] = Field(default_factory=list)
    status: Literal["open", "fixed", "unresolved"] = "open"
    drill: bool = False
    closed_at: datetime | None = None


class Mail(BaseModel):
    """No address: each machine sends only to the recipient in its own settings file."""
    subject: str
    body: str


class MailReceipt(BaseModel):
    sent: bool
    via: str = ""
    detail: str = ""


class Alert(BaseModel):
    title: str
    message: str


class CheckCounter(BaseModel):
    fails: int = 0
    since: datetime | None = None


class Unresolved(BaseModel):
    incident: str
    until: datetime


class WatchState(BaseModel):
    counters: dict[str, CheckCounter] = Field(default_factory=dict)
    unresolved: dict[str, Unresolved] = Field(default_factory=dict)
    wakes: list[datetime] = Field(default_factory=list)     # when the supervisor was woken, last 24 hours
    outbox_tried: datetime | None = None                    # when held mail was last tried


class WatchOutcome(BaseModel):
    results: list[CheckResult]
    incident: str | None = None         # the incident the supervisor was woken for, if any
    lines: list[str] = Field(default_factory=list)


class Ran(BaseModel):
    """One finished child process. A missing program or a timeout is a failed run, not an exception."""
    code: int
    out: str = ""
    err: str = ""

    @property
    def ok(self) -> bool:
        return self.code == 0

    @property
    def said(self) -> str:
        """The most useful short text: stderr if there is any, else stdout, one line."""
        text = (self.err.strip() or self.out.strip()).splitlines()
        return text[-1][:300] if text else f"exit {self.code}"
