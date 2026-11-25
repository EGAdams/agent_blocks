"""IAdwSequenceFeed over the dashboard's one-run debugger feed (GET /api/adw-debugger/run).

The object debugger is served over https by Tailscale, so the page cannot call
the dashboard's plain-http :8765 itself; this service asks on its behalf. Any
failure comes back as `{ ok: False, error }` in the feed's own shape, so the
diagram says the trace cannot be read rather than guessing at the run.
"""
from __future__ import annotations

import json
import os
import urllib.request
from urllib.parse import urlencode

DASHBOARD_URL = os.environ.get( "OBJECT_TRACE_DASHBOARD_URL", "http://127.0.0.1:8765" )


class DashboardSequenceFeed:
    def __init__( self, base_url: str = DASHBOARD_URL, timeout: float = 5.0, opener=urllib.request.urlopen ) -> None:
        self.base_url, self.timeout, self.opener = base_url.rstrip( "/" ), timeout, opener

    def run( self, run_id: str, after: int | None ) -> dict:
        params = { "run_id": run_id, **({ "after": str( after )} if after is not None else {})}
        url = f"{ self.base_url }/api/adw-debugger/run?{ urlencode( params )}"
        try:
            with self.opener( url, timeout=self.timeout ) as reply:
                page = json.loads( reply.read().decode( "utf-8" ))
        except Exception as exc:              # noqa: BLE001 -- fail closed, in the feed's shape
            return { "ok": False, "error": f"the dashboard feed at { self.base_url } did not answer: "
                                           f"{ type( exc ).__name__ }: { str( exc )[ :160 ]}", "runs": [], "events": [] }
        if not isinstance( page, dict ) or "ok" not in page:
            return { "ok": False, "error": "the dashboard feed answered with something that is not a feed page",
                     "runs": [], "events": [] }
        return page
