"""An explicit task outcome separates clarification from action confirmation."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class EmailTaskOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["completed", "needs_information", "failed"]
    message: str = Field(min_length=1)
    action: Literal["read", "draft", "send", "modify"] | None = None


def tool_action(name: str, prefix: str) -> str | None:
    if not name.startswith(prefix):
        return None
    operation = name[len(prefix):].lower()
    if any(word in operation for word in ("send", "reply", "forward")):
        return "send"
    if operation.startswith(("get_", "list_", "search_", "fetch_", "batch_get_", "read_")):
        return "read"
    if any(word in operation for word in ("delete", "trash", "archive", "modify")):
        return "modify"
    if any(word in operation for word in ("draft", "compose")):
        return "draft"
    return "modify"


def outcome_text(outcome: EmailTaskOutcome, confirmed_actions: set[str]) -> str:
    if outcome.status == "needs_information":
        return outcome.message
    if outcome.status == "failed":
        return f"The email task couldn't finish. {outcome.message}"
    if outcome.action not in confirmed_actions:
        return "No email action was confirmed. The connector did not confirm the claimed action."
    return outcome.message
