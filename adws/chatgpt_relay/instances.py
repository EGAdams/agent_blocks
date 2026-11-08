"""The instance Registry: which ChatGPT browser sessions exist, as data rather than as code.

A second instance is one entry here. That is deliberate and it is the point of the whole design: the
request "send a message to another instance of yourself" only makes sense if there is more than one, and
it must not require editing an operation to add one.

`trusted_sender` is the loop control. An instance marked True may be the *origin* of a relayed message
that is then relayed on; an untrusted origin can reach a relay once and no further. Today nothing is
trusted, so the hop limit is one and two instances cannot talk each other into a loop.
"""
from __future__ import annotations

from .models import RelayInstance

#: Chrome on BATI, logged into chatgpt.com with a persistent profile, in front of letta-code's
#: browser_tools/browser_server.py. It survives across sessions because nobody calls /quit on it -- there is
#: deliberately no relay operation that can.
INSTANCES: dict[str, RelayInstance] = {
    "bati": RelayInstance(
        label="bati",
        base_url="http://100.80.49.10:5001",
        owner="EG",
        description="the standing ChatGPT browser session on desktop-shdbati (tailscale), already signed in",
    ),
}
ALIASES = {
    "default": "bati",
    "chatgpt": "bati",
    "desktop-shdbati": "bati",
    "shdbati": "bati",
    "100.80.49.10": "bati",
}
DEFAULT_LABEL = "bati"


class StaticInstanceCatalog:
    """IInstanceCatalog over the INSTANCES table."""

    def __init__(self, table: dict[str, RelayInstance] | None = None,
                 aliases: dict[str, str] | None = None, default: str = DEFAULT_LABEL) -> None:
        self._table = dict(table if table is not None else INSTANCES)
        self._aliases = dict(aliases if aliases is not None else ALIASES)
        self._default = default

    def labels(self) -> list[str]:
        return sorted(self._table)

    def all(self) -> list[RelayInstance]:
        return [self._table[label] for label in self.labels()]

    def resolve(self, name: str | None) -> RelayInstance:
        """A missing name means the default instance. An unknown one is named, with what there is."""
        asked = (name or self._default).strip().lower()
        label = self._aliases.get(asked, asked)
        if label not in self._table:
            known = ", ".join(self.labels()) or "none configured"
            raise InstanceUnknown(f"there is no ChatGPT instance called {name!r}; configured: {known}")
        return self._table[label]


class InstanceUnknown(Exception):
    """The caller named an instance that is not in the registry. A message, never a traceback."""
