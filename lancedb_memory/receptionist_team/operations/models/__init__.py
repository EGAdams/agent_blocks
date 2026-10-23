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
    # Which records the assignment touches. "training" was the only value
    # when tax_issues was the only assignment; widened because live_books
    # and category_spreadsheet read the organization's real books, and a
    # checkpoint that stamps every turn "training" is how she came to
    # believe it of all of them.
    environment: Literal["training", "live"] = "training"
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
