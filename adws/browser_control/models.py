"""The values that cross between Browser Control's pieces. Data only."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class BrowserKind(BaseModel):
    """A browser this machine may start with a DevTools port, as data (today: Chrome only)."""
    model_config = ConfigDict(frozen=True)

    name:    str
    exe:     str          # a WSL path to the Windows executable
    port:    int
    profile: str          # folder under %LOCALAPPDATA%; Chromium refuses a debug port on the everyday profile


#: Chrome is the ONLY authorized browser. There is deliberately no second entry to select, fall back to, or launch.
#: "default" means Chrome with the debug profile: Windows' own default handler can open a link but never be steered.
BROWSERS: dict[str, BrowserKind] = {
    "chrome": BrowserKind(name="chrome", exe="/mnt/c/Program Files/Google/Chrome/Application/chrome.exe",
                          port=9222, profile="adw-chrome-debug"),
}
ALIASES = {"default": "chrome", "google chrome": "chrome", "google-chrome": "chrome"}


class TabView(BaseModel):
    """One open tab. `number` is 1-based, as listed; `id` is the browser's own target id."""
    number: int
    id:     str
    url:    str
    title:  str = ""


class BrowserPlacement(BaseModel):
    """Where Windows says the debug browser is. `known` False means Windows could not be asked."""
    known:    bool
    found:    bool = False        # a browser owns the debug port
    pid:      int = 0
    session:  int = -1            # its Windows session; 0 is the hidden services desktop
    desktops: list[int] = []      # sessions with a desktop (explorer.exe) a person can be looking at
    reason:   str = ""

    @property
    def visible(self) -> bool:
        return self.found and self.session != 0 and self.session in self.desktops
