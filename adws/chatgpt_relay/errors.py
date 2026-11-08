"""The two failures the relay distinguishes, because the right next step differs.

`RelayUnreachable` means nothing answered -- the server is down, tailscale is not up, the host is asleep.
Waiting may fix it; the message was not delivered.

`RelayRefused` means the relay itself declined: an unknown instance, a guard, a message that is not a
message. Repeating it gives the same answer, so a caller must change the request, not retry it.
"""
from __future__ import annotations


class RelayError(Exception):
    """Base, so one `except` in the Operation Template Method turns any of these into a refusal line."""


class RelayUnreachable(RelayError):
    """The browser server could not be spoken to."""


class RelayRefused(RelayError):
    """The relay declined on purpose."""
