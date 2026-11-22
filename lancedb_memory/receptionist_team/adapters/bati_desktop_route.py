"""BatiDesktopRoute -- Chrome on BATI's own desktop goes to bridge_bati_desktop.

"Open the dashboard on BATI and show the Plan for Today" names a machine and a browser. Left to the model it can
land on bridge_browser, whose Chrome is SQMC's, or on Scala's filing route, which also listens for "BATI". Neither
may drive BATI's desktop, and nothing may be rerouted to another machine, so this is matched on the latest user
message and goes ahead of both. A match only reaches the specialist, which still reads a step back and waits for a
yes before anything is sent.

It fires when the message names BATI together with a desktop/browser word (Chrome, browser, desktop, screen,
screenshot, tab, dashboard, Plan for Today). A task id or a money question is left to its own route.
"""
import re

from .latest_user import latest_user_message

_BATI = re.compile(r"\bbati(?:'s)?\b|\bdesktop-shdbati\b", re.I)
_DESKTOP = re.compile(r"\b(?:chrome|browser|desktop|screen|screenshot|tabs?|dashboard|plan\s+for\s+today)\b", re.I)
_TASK_ID = re.compile(r"\bt_[0-9a-f]{24}\b")
_MONEY = re.compile(r"\b(?:spen[dt]|cost|paid|expenses?|budget)\b|\$", re.I)


def bati_desktop_request(text: str) -> bool:
    if not text or _TASK_ID.search(text) or _MONEY.search(text):
        return False
    return bool(_BATI.search(text) and _DESKTOP.search(text))


class BatiDesktopRoute:
    def __init__(self, agent_name: str = "bridge_bati_desktop") -> None:
        self._agent = agent_name

    def forced_agent(self, prompt: str) -> str | None:
        return self._agent if bati_desktop_request(latest_user_message(prompt)) else None

    def question(self, prompt: str) -> str:
        return latest_user_message(prompt)
