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


class OptionView(BaseModel):
    """One item of a dropdown, as data. A `<select>`'s choices are on the screen, so none of this is private."""
    position: int = 0
    value:    str = ""
    text:     str = ""
    selected: bool = False
    disabled: bool = False


class FormControlView(BaseModel):
    """One control a person could operate, with the stable ways of naming it and whether it can be operated now.

    `role` is text | password | dropdown | checkbox | radio | button | editable. A field's typed text is never
    here: a password field comes back `secret` and empty, and every other field says only whether it holds
    something (`filled`). A dropdown carries its options and the one chosen now, which is what makes
    browser.select_option possible without guessing.
    """
    role:     str
    tag:      str = ""
    type:     str = ""
    name:     str = ""
    id:       str = ""
    label:    str = ""
    css:      str = ""
    text:     str = ""                 # a button's caption only
    visible:  bool = True
    enabled:  bool = True
    readonly: bool = False
    required: bool = False
    secret:   bool = False
    filled:   bool = False
    options:  list[OptionView] = []
    selected:       str = ""           # the chosen option's text
    selected_value: str = ""
    #: A dropdown the site draws itself (role=combobox, aria-haspopup) rather than a <select>. Its items
    #: exist in the page only while it is open, so `options` is empty here and select_option opens it first.
    site_drawn: bool = False


class FillVerdict(BaseModel):
    """Did the field take what was typed? `ok` False is a refusal with this `cause`; `note` is the
    sentence that says what the field actually did, whether it took the text or not."""
    model_config = ConfigDict(frozen=True)

    ok:    bool
    cause: str = ""
    note:  str = ""
