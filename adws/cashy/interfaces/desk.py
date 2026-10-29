"""The dashboard's side of Cashy: her conversation as a loopback sidecar
speaking receptionist_team's NDJSON turn contract."""
from __future__ import annotations

from typing import Any, Protocol

from ..models import DeskHandoff


class IDeskRecord( Protocol ):
    """One NDJSON line of a turn (receptionist_team.service_models.TurnRecord)."""

    def model_dump_json( self ) -> str: ...


class ICashyDesk( Protocol ):
    """One conversation as the dashboard's Input Options box sees it."""

    def turn( self, text: str ) -> list[ IDeskRecord ]: ...

    def health( self ) -> dict[ str, Any ]: ...

    def compact( self ) -> dict[ str, Any ]: ...

    def alerts( self ) -> dict[ str, Any ]: ...


class IHandoffDetector( Protocol ):
    """Strategy: does this sentence ask for another agent's window?"""

    def detect( self, text: str ) -> DeskHandoff | None: ...


class IDebugChromeProbe( Protocol ):
    """Is the debug Chrome's DevTools port answering? Never raises."""

    def reachable( self ) -> bool: ...
