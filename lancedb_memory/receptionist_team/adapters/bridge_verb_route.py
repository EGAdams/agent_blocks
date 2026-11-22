"""BridgeVerbRoute -- plainly worded Bridge work goes to its specialist without the route being named.

2026-11-17, Sydney's verify pass: she answered from memory or picked the read-only desk unless the request said
"Use bridge_X". BridgeDeviceRoute already covers scans, prints, notes and code changes (on Toyota's desk too);
this covers the rest of what only a Bridge specialist can do, in words that mean nothing else on Sydney's desk:

* bridge_browser -- download a URL or a file, list/close/switch tabs, read the page, click a link or button,
  fill a box or field, choose from a dropdown, navigate a tab. "Open <url>" is NOT here: her browser_opener
  opens links in the user's own browser, and that choice stays the model's.
* bridge_chatgpt -- ask / tell / send to ChatGPT, or anything naming the ChatGPT relay.
* bridge_deploy  -- deploy, restart, roll back, health, logs or history of a project in the fixed table.
* bridge_scala_storage -- Scala storage, or its storage targets / machines.
* bridge_scanner -- run / use the scanner, scan it again, scan what's in the scanner (2026-11-22: "Have Scala run
  the scanner again" matched nothing, and her model asked for a systemd service name instead). A request naming
  the window or freezer scanner is BridgeDeviceRoute's, on both desks.

A task id stays with BridgeTaskRoute (asked earlier), and a message about money is left alone, as in
BridgeDeviceRoute. Sydney only (profiles.SYDNEY); Toyota's panel is unchanged. Goes after the post office route,
so "ask Sonnet to write code that clicks the button" is a job for Sonnet, not a click. A match only reaches the
specialist; on Sydney's desk it is sent at once, with no yes (profiles._bridge_runners, confirm=False).
"""
import re

from .latest_user import latest_user_message

_TASK_ID = re.compile(r"\bt_[0-9a-f]{24}\b")
_MONEY = re.compile(r"\b(?:spen[dt]|cost|paid|expenses?|budget|bought)\b|\$", re.I)
_PROJECTS = r"(?:agent_blocks|agent\s+blocks|dashboard|bridge_sandbox|bridge\s+sandbox|gateway)"

_ROUTES = (
    ("bridge_chatgpt", re.compile(
        r"\b(?:ask|tell|message|send\s+(?:a\s+message\s+|this\s+|that\s+|it\s+)?to)\s+chatgpt\b|"
        r"\bchatgpt(?:'s)?\s+(?:relay|thread|browser\s+session|sessions?)\b|\brelay\b.{0,40}\bchatgpt\b", re.I | re.S)),
    ("bridge_scanner", re.compile(
        r"\b(?:run|use|start|fire\s+up|try)\s+(?:the\s+)?scanner\b|\bscanner\s+again\b|"
        r"\bscan\s+(?:it|this|that|again|the\s+(?:document|page|paper|title|form|letter))\b|"
        r"\bscan\s+what(?:'s|\s+is)\s+in\b|\bwhat(?:'s|\s+is)\s+in\s+the\s+scanner\b", re.I)),
    ("bridge_scala_storage", re.compile(r"\bscala(?:'s)?\s+storage\b|\bstorage\s+(?:targets?|machines?)\b", re.I)),
    ("bridge_deploy", re.compile(
        rf"\b(?:re)?deploy\b.{{0,40}}\b{_PROJECTS}\b|\broll\s?back\b.{{0,40}}\b{_PROJECTS}\b|"
        rf"\b{_PROJECTS}(?:'s)?\s+(?:deploy\w*|health|logs|deployment\s+history|release)\b|"
        rf"\b(?:health|logs|deployment\s+history)\s+(?:check\s+)?(?:of|for|on)\s+(?:the\s+)?{_PROJECTS}\b", re.I | re.S)),
    ("bridge_browser", re.compile(
        r"\bdownload\b.{0,80}(?:https?://|\b(?:file|link|pdf|tif|tiff|zip|attachment|document|form)\b)|"
        r"\b(?:what|which|list|show)\b.{0,30}\btabs?\b.{0,20}\b(?:open|browser|chrome)\b|"
        r"\b(?:list|show)\s+(?:me\s+)?(?:the\s+|my\s+)?(?:open\s+|browser\s+|chrome\s+)?tabs\b|"
        r"\b(?:close|switch\s+to|go\s+to)\s+(?:the\s+)?\S+(?:\s+\S+)?\s+tab\b|"
        r"\bread\s+(?:me\s+)?(?:the\s+|this\s+|that\s+|my\s+)?(?:current\s+|open\s+)?(?:web\s?)?(?:page|tab)\b|"
        r"\bclick\b.{0,80}\b(?:link|button|checkbox)\b|\bclick\s+(?:on\s+)?[\"“']|"
        r"\b(?:fill(?:\s+in)?|type)\b.{0,60}\b(?:box|field)\b|\b(?:choose|select|pick)\b.{0,60}\b(?:dropdown|drop-down)\b|"
        r"\bform\s+controls\b|\bnavigate\b.{0,40}\b(?:tab|browser|chrome)\b|\bnavigate\s+to\s+https?://", re.I | re.S)),
)


def verb_route(text: str) -> str | None:
    if not text or _TASK_ID.search(text) or _MONEY.search(text):
        return None
    return next((name for name, pattern in _ROUTES if pattern.search(text)), None)


class BridgeVerbRoute:
    def forced_agent(self, prompt: str) -> str | None:
        return verb_route(latest_user_message(prompt))

    def question(self, prompt: str) -> str:
        return latest_user_message(prompt)
