"""HttpsBridgeTransport -- the only module here that opens a socket.

- HTTPS only (BridgeClientConfig refuses anything else);
- redirects are never followed, so the bearer token can't be carried to a
  second host -- a 3xx is BridgeMalformed;
- the response body is read up to a bound, never more;
- every error message is passed through sanitize() with the token as a known
  secret, so an upstream echo of the Authorization header can't leak.
"""
import json
import socket
import urllib.error
import urllib.request
from typing import Any

from .errors import (BridgeAmbiguous, BridgeAuthError, BridgeMalformed, BridgeRefused, BridgeTimeout,
                     BridgeUnavailable, sanitize)
from .models import BridgeClientConfig

USER_AGENT = "toyota-bridge-client/1.0"  # HostGator returns 406 to urllib's default
MAX_REQUEST_BYTES = 65536


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401 - urllib hook
        return None


class HttpsBridgeTransport:
    """IBridgeTransport over urllib, with the token held privately."""

    def __init__(self, config: BridgeClientConfig, token: str, opener: urllib.request.OpenerDirector | None = None) -> None:
        if not token:
            raise BridgeUnavailable("no_credential", "no client credential is configured")
        self._config = config
        self._token = token
        self._opener = opener or urllib.request.build_opener(_NoRedirect())

    def _clean(self, text: object) -> str:
        return sanitize(text, (self._token,))

    def _read(self, response: Any) -> bytes:
        data = response.read(self._config.max_response_bytes + 1)
        if len(data) > self._config.max_response_bytes:
            raise BridgeMalformed("too_large", "the Bridge's response exceeded the size limit")
        return data

    def _decode(self, status: int, data: bytes) -> Any:
        try:
            return json.loads(data.decode("utf-8")) if data else {}
        except (UnicodeDecodeError, ValueError):
            raise BridgeMalformed("not_json", f"HTTP {status} with a body that is not JSON", status) from None

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
        if not path.startswith("/") or "//" in path or ".." in path or path.startswith("/worker"):
            raise BridgeUnavailable("bad_path", "refusing a path outside the client API")
        payload = None
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json", "User-Agent": USER_AGENT}
        if body is not None:
            payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
            if len(payload) > MAX_REQUEST_BYTES:
                raise BridgeRefused("too_large", "the request is larger than the Bridge accepts")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self._config.base_url + path, data=payload, method=method, headers=headers)
        try:
            with self._opener.open(req, timeout=self._config.timeout_seconds) as response:
                status = response.status
                if 300 <= status < 400:
                    raise BridgeMalformed("redirect", f"HTTP {status} redirect refused", status)
                return status, self._decode(status, self._read(response))
        except urllib.error.HTTPError as exc:
            status = exc.code
            if 300 <= status < 400:
                raise BridgeMalformed("redirect", f"HTTP {status} redirect refused", status) from None
            try:
                parsed = self._decode(status, self._read(exc))
            except BridgeMalformed:
                parsed = {}
            error = parsed.get("error") if isinstance(parsed, dict) else None
            code = str(error.get("code", "")) if isinstance(error, dict) else ""
            message = self._clean(error.get("message", "")) if isinstance(error, dict) else ""
            code = self._clean(code) or f"http_{status}"
            # A 403 not_allowed is policy refusing this one task (a missing scope, a protected agent), not the
            # credential: say the gateway's reason, or a scanner scope gap reads as "check Toyota's token".
            if status == 403 and code == "not_allowed":
                raise BridgeRefused(code, message or "the Bridge does not allow that task", status) from None
            if status in (401, 403):
                raise BridgeAuthError(code, message or "the Bridge refused the credential", status) from None
            if status == 429 or status >= 500:
                raise BridgeAmbiguous(code, message or f"HTTP {status}", status) from None
            raise BridgeRefused(code, message or f"HTTP {status}", status) from None
        except (socket.timeout, TimeoutError):
            raise BridgeTimeout("timeout", f"no answer within {self._config.timeout_seconds:g}s") from None
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise BridgeTimeout("timeout", f"no answer within {self._config.timeout_seconds:g}s") from None
            raise BridgeAmbiguous("unreachable", self._clean(reason)) from None
        except (ConnectionError, OSError) as exc:
            raise BridgeAmbiguous("unreachable", self._clean(exc)) from None
