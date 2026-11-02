"""Email operations always reach live tools instead of recalled mailbox facts."""
import re
from .latest_user import latest_user_message

_EMAIL = re.compile(r"\b(e-?mail|gmail|outlook|inbox|mailbox|draft|attachment|unread|spam|sender|recipient|forward|reply|send|compose|archive|delete)\w*\b", re.I)


class EmailRequestRoute:
    def forced_agent(self, prompt: str) -> str | None:
        text = latest_user_message(prompt)
        if text.startswith("[Message for Yolanda from ") or _EMAIL.search(text):
            return "email_tools"
        return None

    def question(self, prompt: str) -> str:
        return latest_user_message(prompt)
