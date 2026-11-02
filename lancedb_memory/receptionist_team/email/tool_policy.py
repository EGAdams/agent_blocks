"""Scope the tool session to email and the actions requested in this task."""
import re


class EmailToolPolicy:
    def __init__(self, prefix: str, request: str) -> None:
        self.prefix = prefix
        self.request = re.sub(r"^\[Message for Yolanda from [^\]]+\]\s*", "", request)

    def problem(self, name: str) -> str | None:
        if name in {"ToolSearch", "StructuredOutput"}:
            return None
        if not name.startswith(self.prefix):
            return "Yolanda uses only the configured email connector."
        operation = name[len(self.prefix):].lower()
        # Sending a saved draft is sending, not drafting.
        if re.search(r"send|reply|forward", operation):
            if re.search(r"\b(don't|do not|never)\s+(send|reply|forward)\b|\bdraft only\b", self.request, re.I):
                return "The current task does not authorize sending."
            if re.search(r"\b(draft|compose|prepare)\b", self.request, re.I) and not re.search(r"\bsend\b", self.request, re.I):
                return "Drafting a reply does not authorize sending it."
            if not re.search(r"\b(send|reply|forward)\b|^(?:please\s+)?e-?mail\b|\b(?:can|could|would) you e-?mail\b", self.request, re.I):
                return "Sending requires an explicit send, reply or forward request in the current task."
            return None
        if operation.startswith(("get_", "list_", "search_", "fetch_", "batch_get_", "read_")):
            return None
        if re.search(r"delete|trash|archive", operation):
            return None if re.search(r"\b(delete|trash|archive|move)\b", self.request, re.I) else "Deleting or archiving was not requested."
        if re.search(r"draft|compose", operation):
            return None if re.search(r"\b(draft|compose|write|prepare|send|reply|forward)\b", self.request, re.I) else "Drafting was not requested."
        if re.search(r"delete|trash|archive|modify|update|create_label|label|mark", operation):
            return None if re.search(r"\b(delete|trash|archive|label|mark|move|organize|rename)\b", self.request, re.I) else "Changing the mailbox was not requested."
        return "This email operation has no installed authorization rule."

    async def hook(self, data, tool_use_id, context):
        problem = self.problem(data.get("tool_name", ""))
        if problem:
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                    "permissionDecision": "deny", "permissionDecisionReason": problem}}
        return {}
