"""IRecorderClient over the service's HTTP control API (stdlib only, so every
ADW can use it without aiohttp)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from ..errors import BrowserWorkflowError
from ..models import ControllerStatus


class HttpRecorderClient:
    def __init__( self, base_url: str = "http://127.0.0.1:8781", timeout_seconds: float = 20.0 ) -> None:
        self._base    = base_url.rstrip( "/" )
        self._timeout = timeout_seconds

    def status( self ) -> ControllerStatus | None:
        try:
            return ControllerStatus.model_validate( self._call( "GET", "/recording/status" ))
        except ( urllib.error.URLError, ConnectionError, TimeoutError, OSError ):
            return None

    def start( self ) -> ControllerStatus:
        return self._post( "/recording/start", {})

    def stop( self ) -> ControllerStatus:
        return self._post( "/recording/stop", {})

    def save( self, name: str, description: str = "" ) -> ControllerStatus:
        return self._post( "/recording/save", { "name": name, "description": description })

    def discard( self ) -> ControllerStatus:
        return self._post( "/recording/discard", {})

    def capture( self, event: dict ) -> ControllerStatus:
        return self._post( "/recording/capture", { "event": event })

    def _post( self, path: str, body: dict[ str, Any ]) -> ControllerStatus:
        return ControllerStatus.model_validate( self._call( "POST", path, body ))

    def _call( self, method: str, path: str, body: dict[ str, Any ] | None = None ) -> dict[ str, Any ]:
        data = json.dumps( body ).encode() if body is not None else None
        req  = urllib.request.Request( self._base + path, data=data, method=method,
                                       headers={ "Content-Type": "application/json" } if data is not None else {})
        try:
            with urllib.request.urlopen( req, timeout=self._timeout ) as resp:
                return json.loads( resp.read() or b"{}" )
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads( exc.read() or b"{}" )
            except ValueError:
                payload = {}
            if "state" in payload:
                return payload
            raise BrowserWorkflowError( payload.get( "error" ) or f"recorder service said HTTP { exc.code }" ) from exc
