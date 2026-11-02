"""Only Yolanda operates email; other front desks forward through the post office."""
import re
from .latest_user import latest_user_message

_EMAIL = re.compile(r"\b(e-?mail|gmail|outlook|inbox|mailbox|yolanda)\b", re.I)


class EmailDeskRoute:
    def __init__(self, post_office: str) -> None:
        self.post_office = post_office

    def forced_agent(self, prompt: str) -> str | None:
        text = latest_user_message(prompt)
        if re.search(r"\b(open|switch)\b.*\byolanda\b", text, re.I):
            return None
        return self.post_office if _EMAIL.search(text) else None

    def question(self, prompt: str) -> str:
        message = latest_user_message(prompt)
        return f"yolanda: {message}"
