"""Pydantic shapes for the Bridge's public client API (v1), and the route config.

Requests are extra="forbid" because the gateway rejects unknown fields too;
responses ignore extras (the server may add some) but fail on anything missing
or mistyped, which the client reports as BridgeMalformed.
"""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

AGENT_PATTERN = r"^[a-z][a-z0-9-]{0,40}$"
WORKSPACE_PATTERN = r"^[a-z][a-z0-9_-]{0,40}$"
TASK_ID_PATTERN = r"^t_[0-9a-f]{24}$"
IDEMPOTENCY_PATTERN = r"^[A-Za-z0-9._:-]{1,128}$"

# The only operations Toyota may ever request. `implement` (workspace_write)
# is not in this set and cannot be configured into it.
READ_ONLY_OPERATIONS = frozenset({"inspect", "explain", "search_memory"})
ReadOnlyOperation = Literal["inspect", "explain", "search_memory"]
TaskState = Literal["queued", "running", "succeeded", "failed", "cancelled"]
TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled"})


class BridgeRoute(BaseModel):
    """The one configured route: which Bridge agent, workspace and operation.

    Fixed by config (toyota_bridge_route.json), never by the model or the
    user's words, so voice intent cannot pick a workspace or a write mode.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    agent: str = Field(pattern=AGENT_PATTERN)
    workspace: str = Field(pattern=WORKSPACE_PATTERN)
    operation: ReadOnlyOperation = "inspect"
    approval_mode: Literal["read_only"] = "read_only"


class BridgePolicy(BaseModel):
    """What Toyota may ask the Bridge for (toyota_bridge_route.json).

    Names no agent list: the Bridge's GET /agents is the list. It only says
    which agent gets a request that doesn't name one, where that agent looks by
    default, and which operations may ever be sent -- read-only ones only, so
    implement / print / scan can't be configured in.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    default_agent: str = Field(pattern=AGENT_PATTERN)
    default_workspace: str = Field(pattern=WORKSPACE_PATTERN)
    allowed_operations: tuple[ReadOnlyOperation, ...] = ("inspect", "explain", "search_memory")
    approval_mode: Literal["read_only"] = "read_only"

    @field_validator("allowed_operations")
    @classmethod
    def _some_and_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or len(set(value)) != len(value):
            raise ValueError("allowed_operations must list at least one operation, once each")
        return value


class BridgeTaskRequest(BaseModel):
    """POST /tasks body. Read-only operations only."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    task_type: Literal["agent_task"] = "agent_task"
    agent: str = Field(pattern=AGENT_PATTERN)
    workspace: str = Field(pattern=WORKSPACE_PATTERN)
    operation: ReadOnlyOperation
    prompt: str = Field(min_length=1, max_length=8000)
    approval_mode: Literal["read_only"] | None = "read_only"
    idempotency_key: str | None = Field(default=None, pattern=IDEMPOTENCY_PATTERN)

    @field_validator("prompt")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt is blank")
        return value

    def body(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


# The gateway decides each operation's mode (Bootstrap::OPERATION_MODES); this
# mirrors it so a specialist's request names the one mode the gateway accepts.
OPERATION_MODES: dict[str, str] = {
    "inspect": "read_only", "explain": "read_only", "search_memory": "read_only",
    "implement": "workspace_write", "scan": "device_control", "print": "device_control",
    "print_cancel": "device_control", "print_status": "read_only", "print_discover": "read_only",
}
# What changes a file or moves paper: Toyota reads these back and waits for a yes.
CONFIRM_OPERATIONS = frozenset({"implement", "scan", "print", "print_cancel"})
ActionOperation = Literal["inspect", "explain", "search_memory", "implement", "scan",
                          "print", "print_cancel", "print_status", "print_discover"]


class BridgeActionRequest(BaseModel):
    """POST /tasks body from a Bridge specialist: any operation, in the gateway's own mode.

    BridgeTaskRequest above stays read-only for the star_trek_bridge desk. This
    one is built only by bridge_client/specialists.py, from a form a specialist
    filled and code checked, never from free text.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    task_type: Literal["agent_task"] = "agent_task"
    agent: str = Field(pattern=AGENT_PATTERN)
    workspace: str = Field(pattern=WORKSPACE_PATTERN)
    operation: ActionOperation
    prompt: str = Field(min_length=1, max_length=8000)
    approval_mode: Literal["read_only", "workspace_write", "device_control"]
    idempotency_key: str | None = Field(default=None, pattern=IDEMPOTENCY_PATTERN)

    @model_validator(mode="after")
    def _mode_matches_operation(self) -> "BridgeActionRequest":
        if not self.prompt.strip():
            raise ValueError("prompt is blank")
        if OPERATION_MODES[self.operation] != self.approval_mode:
            raise ValueError(f"{self.operation} runs {OPERATION_MODES[self.operation]}")
        return self

    @property
    def needs_confirmation(self) -> bool:
        return self.operation in CONFIRM_OPERATIONS

    def body(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class BridgeAgent(BaseModel):
    """One entry of GET /agents."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    name: str
    display_name: str = ""
    description: str = ""
    operations: list[str]
    workspaces: list[str]
    write_workspaces: list[str] = Field(default_factory=list)


class BridgeAgentList(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    agents: list[BridgeAgent]


class BridgeEvent(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    seq: int = Field(ge=1)
    kind: str = Field(max_length=40)
    message: str = Field(max_length=2000)
    at: int


class BridgeTaskView(BaseModel):
    """POST /tasks and GET /tasks/{id}."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    task_id: str = Field(pattern=TASK_ID_PATTERN)
    state: TaskState
    agent: str
    workspace: str
    operation: str
    approval_mode: str | None = None
    attempts: int = 0
    cancel_requested: bool = False
    last_error: str | None = None
    replayed: bool = False
    recent_events: list[BridgeEvent] | None = Field(default=None, max_length=50)

    @property
    def events_available(self) -> bool:
        return self.recent_events is not None

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES


class BridgeResult(BaseModel):
    """GET /tasks/{id}/result. ready=False is never a success."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    task_id: str = Field(pattern=TASK_ID_PATTERN)
    state: TaskState
    ready: bool
    ok: bool | None = None
    summary: str | None = None
    body: str | None = None
    performer: str | None = None
    last_error: str | None = None
    operation: str | None = None
    workspace: str | None = None
    approval_mode: str | None = None
    details: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _ready_means_an_outcome(self) -> "BridgeResult":
        if self.ready and self.ok is None:
            raise ValueError("a ready result must say ok")
        if self.ready and self.state not in TERMINAL_STATES:
            raise ValueError("a ready result must be in a terminal state")
        return self

    @property
    def succeeded(self) -> bool:
        return self.ready and self.ok is True and self.state == "succeeded"


class BridgeClientConfig(BaseModel):
    """Where the client API is and how patient to be. No credential in here."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: str = "https://bridge.americansjewelry.com/api/agent-bridge/v1"
    timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    max_response_bytes: int = Field(default=300_000, gt=0, le=2_000_000)

    @field_validator("base_url")
    @classmethod
    def _https_only(cls, value: str) -> str:
        value = value.rstrip("/")
        if not value.startswith("https://") or "@" in value.split("/")[2]:
            raise ValueError("the Bridge URL must be https:// with no embedded credentials")
        if "/worker" in value:
            raise ValueError("the client never talks to the /worker API")
        return value


class DownloadedFile(BaseModel):
    """One file a browser.download task saved, as the gateway describes it (BrowserTools::about)."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    file_id: str
    task_id: str = ""
    name: str = ""
    original_name: str = ""
    mime: str = ""
    bytes: int = 0
    kind: str = ""
    page_count: int = 0
    text_chars: int = 0
    text_truncated: bool = False
    note: str = ""
    saved_at: str = ""


class DownloadedFileList(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    files: list[DownloadedFile] = Field(default_factory=list)
    next: str = ""


class FileView(DownloadedFile):
    """`browser_view_file`'s facts and one chunk of text. Its page image is never kept here."""

    text: str = ""
    text_offset: int = 0
    text_more: bool = False
    next_text_offset: int | None = None
    rendered_pages: list[int] = Field(default_factory=list)
    notice: str = ""
