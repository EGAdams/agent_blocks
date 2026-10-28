"""The recorder extension's fixed ID, worked out from the public key its
manifest pins -- the same arithmetic Chrome does -- so the service's Origin
check and the manifest can never drift apart."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

MANIFEST_PATH = Path( __file__ ).resolve().parent / "chrome_extension" / "manifest.json"


def extension_id( manifest_path: Path = MANIFEST_PATH ) -> str:
    key    = json.loads( Path( manifest_path ).read_text( encoding="utf-8" ))[ "key" ]
    digest = hashlib.sha256( base64.b64decode( key )).hexdigest()[ :32 ]
    return "".join( chr( ord( "a" ) + int( c, 16 )) for c in digest )


def extension_origin( manifest_path: Path = MANIFEST_PATH ) -> str:
    return f"chrome-extension://{ extension_id( manifest_path ) }"
