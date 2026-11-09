"""BridgeDeviceRoute -- scans, prints and Bridge code changes go to their specialist.

Toyota's model sent "scan a receipt on the window scanner" to the read-only
star_trek_bridge desk, which can only refuse it. These requests name their
device or Bridge agent plainly, so they are matched on the latest user
message, not left to the model. A match only reaches a specialist, which
still reads the request back and waits for a yes before anything is sent.

Goes right after BridgeConfirmationRoute and ahead of Scala's route: "scan
this for Scala" is a scan.
"""
import re

from .latest_user import latest_user_message

# "What did we spend on printers" is a books question, not a print job.
_MONEY = re.compile(r"\b(?:spen[dt]|cost|paid|pay|expenses?|totals?|budget|receipts? for|bought)\b|\$", re.I)
_TASK_ID = re.compile(r"\bt_[0-9a-f]{24}\b")
# "Explain what the notes diagnostic proved" is a question for Toyota, not a job for the device.
_NOTES_ACTION = re.compile(r"\b(?:run|write|check|clear|remove|delete|start|status|new)\b", re.I)
# Approved notes (the deed, or any note named by its .md file name): list, read, change.
_APPROVED_NOTES = re.compile(r"\b[\w.-]+\.md\b|\b(?:the\s+)?deed\b|\bapproved\s+notes?\b|"
                             r"\b(?:list|show)\s+(?:me\s+)?(?:the\s+|my\s+)?notes\b|"
                             r"\b(?:search|find|look\s+through)\s+(?:through\s+)?(?:the\s+|my\s+|all\s+)?notes\b|"
                             r"\bnotes?\s+trash\b|\btrash(?:ed)?\s+notes?\b|\b(?:in|from|to)\s+the\s+trash\b|"
                             r"\b(?:create|make|add)\s+(?:a\s+)?new\s+note\b|"
                             r"\bhighlights?\b.{0,60}\bnotes?\b|\bnotes?\b.{0,60}\bhighlights?\b", re.I | re.S)
_APPROVED_ACTION = re.compile(r"\b(?:list|show|read|open|update|edit|change|fix|replace|correct|remove|delete|"
                              r"insert|add|rename|highlight|unhighlight|clear|search|find|create|make|trash|restore|"
                              r"purge|move|refresh|permanently|changed|modified|updated)\b", re.I)
# Toyota's model sent "ask the Bridge browser to download ..." to Sydney and to the read-only desk on
# successive tries (2026-11-09). Named outright, or about files a download saved, it is bridge_browser.
_BROWSER = re.compile(r"\bbridge(?:'s)?\s+browser\b|\bdownloaded\s+files?\b|\bf_[0-9a-f]{24}\b|"
                      r"\b(?:my|your|our|the)\s+(?:bridge\s+)?downloads\b|"
                      r"\b(?:files?|zip|tif|pdf)\s+(?:have\s+|did\s+)?(?:i|you|we)\s+(?:just\s+)?(?:downloaded|download)\b", re.I)
_ROUTES = (
    ("bridge_browser", _BROWSER),
    ("bridge_scanner", re.compile(r"\b(?:window|freezer)\s+scanner\b|\bscan\b.*\b(?:window|freezer)\b"
                                  r"|\b(?:start|do|run)\s+a\s+scan\b", re.I | re.S)),
    ("bridge_notes", re.compile(r"\bnotes?\s+diagnostics?\b|\bNOTESDIAG-[0-9a-f]{12}\b|\bdiagnostic\s+(?:note|marker)\b", re.I)),
    ("bridge_notes", _APPROVED_NOTES),
    ("bridge_printer", re.compile(r"\bprinters?\b|\btest\s+page\b|\bprint\s+job\b", re.I)),
    ("bridge_writer", re.compile(r"\bcodex[\s-]+writer\b|\bbridge[\s_]+sandbox\b", re.I)),
    ("bridge_coder", re.compile(r"\b(?:claude[\s-]+)?sonnet\b.*\bbridge\b|\bbridge\b.*\b(?:claude[\s-]+)?sonnet\b",
                                re.I | re.S)),
)


def device_route(text: str) -> str | None:
    if not text or _TASK_ID.search(text) or _MONEY.search(text):  # status / result / cancel of a task stays with star_trek_bridge
        return None
    name = next((name for name, pattern in _ROUTES if pattern.search(text)), None)
    if name == "bridge_notes" and not (_NOTES_ACTION.search(text) or
                                       (_APPROVED_NOTES.search(text) and _APPROVED_ACTION.search(text))):
        return None
    return name


class BridgeDeviceRoute:
    def forced_agent(self, prompt: str) -> str | None:
        return device_route(latest_user_message(prompt))

    def question(self, prompt: str) -> str:
        return latest_user_message(prompt)
