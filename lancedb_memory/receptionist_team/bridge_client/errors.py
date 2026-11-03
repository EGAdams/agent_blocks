"""What can go wrong talking to the Bridge, as distinct types, never as text to parse.

Every message here is already sanitized: an upstream error can echo an
Authorization header or the credential itself, so nothing reaches a log, a
reply or an exception without passing through sanitize() first.
"""
import re
from collections.abc import Iterable

_BEARER = re.compile(r"(?i)\bbearer\s+\S+")
_LONG_SECRETISH = re.compile(r"\b[A-Za-z0-9_\-]{40,}\b")
MESSAGE_LIMIT = 200


def sanitize(text: object, secrets: Iterable[str] = ()) -> str:
    """Strip bearer values, any known secret and long token-shaped runs; clip."""
    out = str(text or "")
    for secret in secrets:
        if secret:
            out = out.replace(secret, "[redacted]")
    out = _BEARER.sub("Bearer [redacted]", out)
    out = _LONG_SECRETISH.sub("[redacted]", out)
    out = " ".join(out.split())
    return out if len(out) <= MESSAGE_LIMIT else out[:MESSAGE_LIMIT - 1] + "…"


class BridgeError(Exception):
    """Base class. `code` is the gateway's error code or a local one."""

    def __init__(self, code: str, message: str = "", status: int = 0) -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message
        self.status = status


class BridgeUnavailable(BridgeError):
    """Not configured here (no credential, bad URL): nothing was sent."""


class BridgeAuthError(BridgeError):
    """401 or 403: the credential was refused. Nothing ran."""


class BridgeRefused(BridgeError):
    """A definite 4xx refusal (invalid_task, not_found, idempotency_conflict...)."""


class BridgeMalformed(BridgeError):
    """A response we could not read or that broke the contract (incl. redirects)."""


class BridgeAmbiguous(BridgeError):
    """The request may or may not have taken effect (network, 5xx, 429).

    A submit that ends here is retried only with the SAME idempotency key.
    """


class BridgeTimeout(BridgeAmbiguous):
    """No answer within the per-request timeout."""
