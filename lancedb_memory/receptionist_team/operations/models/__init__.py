"""Runtime-validated HTTP data; financial decisions remain in the finance ADW."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class WorkIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["start", "select", "evidence", "editor", "category", "status", "pause", "cancel", "report"]
    assignment: str = "tax_issues"
    expense_id: int | None = None
    category: str = ""
    instruction: str = ""
    report_type: str = ""
    report_format: str = ""


class WorkView(BaseModel):
    model_config = ConfigDict(extra="allow")
    workspace: str = "toyota"
    assignment: str = "tax_issues"
    environment: Literal["training"] = "training"
    status: str = "idle"
    message: str = ""
    expense_id: int | None = None
    command: dict | None = None


class WorkReply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool
    state: WorkView
    replayed: bool = False
    error: str = ""


class ConversationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    text: str
    request_id: str | None = None
