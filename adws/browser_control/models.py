"""The values that cross between Browser Control's pieces. Data only."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class BrowserKind(BaseModel):
    """One Chromium browser this machine can start with a DevTools port, as data."""
    model_config = ConfigDict(frozen=True)

    name:    str
    exe:     str          # a WSL path to the Windows executable
    port:    int
    profile: str          # folder under %LOCALAPPDATA%; Chromium refuses a debug port on the everyday profile


#: "default" is Chrome: the browser the user's other ADWs already drive. Windows' own default
#: handler can open a link but never be steered afterwards, so it cannot be the controllable one.
BROWSERS: dict[str, BrowserKind] = {
    "chrome": BrowserKind(name="chrome", exe="/mnt/c/Program Files/Google/Chrome/Application/chrome.exe",
                          port=9222, profile="adw-chrome-debug"),
    "edge":   BrowserKind(name="edge", exe="/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
                          port=9223, profile="adw-edge-debug"),
}
ALIASES = {"default": "chrome", "google chrome": "chrome", "microsoft edge": "edge", "msedge": "edge"}


class TabView(BaseModel):
    """One open tab. `number` is 1-based, as listed; `id` is the browser's own target id."""
    number: int
    id:     str
    url:    str
    title:  str = ""
