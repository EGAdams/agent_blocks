"""HelloWorld -- the smallest operation. Proves the ADW harness end to end."""
from __future__ import annotations

from typing import Any

from ._named import Named
from .base import OperationOutcome


class HelloWorld( Named ):
    NAME        = "hello_world"
    DESCRIPTION = "Say Hello World"

    def run( self, args: dict[ str, Any ]) -> OperationOutcome:
        return OperationOutcome( ok=True, line="Hello World", data={ "greeting": "Hello World" })
