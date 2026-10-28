"""IJsonFetcher over urllib."""
from __future__ import annotations

import json
import urllib.request
from typing import Any


class UrllibJsonFetcher:
    def get_json( self, url: str, timeout_seconds: float ) -> Any:
        with urllib.request.urlopen( url, timeout=timeout_seconds ) as response:
            return json.loads( response.read().decode( "utf-8" ))
