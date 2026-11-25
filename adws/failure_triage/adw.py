"""What the status and fix ADWs share: an id, the desk, and one way to finish."""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any


def new_id() -> str:
    return uuid.uuid4().hex[ :8 ]


def finish( json_out: str | None, ok: bool, line: str, data: dict[ str, Any ] | None = None, adw_id: str = "",
            as_json: bool = False ) -> int:
    answer = { "ok": ok, "verified": ok, "line": line, "data": data or {}, "adw_id": adw_id, "retryable": False }
    if json_out:
        path = Path( json_out )
        path.parent.mkdir( parents=True, exist_ok=True )
        path.write_text( json.dumps( answer, indent=2, default=str ), encoding="utf-8" )
    print( json.dumps( answer, default=str ) if as_json else line, flush=True )
    return 0 if ok else 1
