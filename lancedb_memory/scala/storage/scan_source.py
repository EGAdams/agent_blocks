"""IScanSource over the dashboard's scan façade client (agent_bridge_worker's HttpScanFacade), so the original
bytes come from the authorized scanner API, by scan id, with the same loopback-only token rules."""
from __future__ import annotations

from typing import Any, Protocol

from .interfaces import StorageRefused


class IScanFacadeReads( Protocol ):
    def document( self, scan_id: str ) -> dict: ...
    def original( self, scan_id: str ) -> bytes: ...
    def recent( self, limit: int ) -> dict: ...


class FacadeScanSource:
    def __init__( self, facade: IScanFacadeReads ) -> None:
        self._facade = facade

    def document( self, scan_id: str ) -> dict:
        return self._call( self._facade.document, scan_id )

    def original( self, scan_id: str ) -> bytes:
        return self._call( self._facade.original, scan_id )

    def latest( self ) -> str:
        scans = self._call( self._facade.recent, 10 ).get( "scans", [])
        for scan in scans:
            if scan.get( "status" ) == "captured" and scan.get( "artifact" ):
                return str( scan[ "scan_id" ])
        raise StorageRefused( "no_scan", "the scanner has no captured scan to file" )

    @staticmethod
    def _call( method: Any, value: Any ) -> Any:
        try:
            return method( value )
        except ValueError as exc:
            raise StorageRefused( "invalid_scan_id", str( exc )) from None
        except Exception as exc:                    # the façade's GatewayError carries .code
            code = getattr( exc, "code", type( exc ).__name__ )
            raise StorageRefused( f"scanner_{ code }", f"the scanner API refused: { exc }"[ :300 ]) from None
