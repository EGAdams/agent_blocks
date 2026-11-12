"""The roles Scala's machine-aware storage is written against. Concretes are named only in the ADW's build function."""
from __future__ import annotations

from typing import Any, Protocol

from .models import AuditRecord, StorageOutcome, StorageTarget


class StorageRefused( Exception ):
    """A request Scala will not carry out: unknown machine, folder not approved, identity mismatch, conflict..."""

    def __init__( self, code: str, message: str, **extra: Any ) -> None:
        super().__init__( message )
        self.code, self.message, self.extra = code, message, extra


class TargetUnreachable( Exception ):
    """The destination could not be reached at all. Nothing was written there; retry later."""


class IScanSource( Protocol ):
    """The scanner's own record of a scan, found by scan id only -- never by a path."""

    def document( self, scan_id: str ) -> dict: ...     # mime_type, size_bytes, sha256, captured_at
    def original( self, scan_id: str ) -> bytes: ...
    def latest( self ) -> str: ...                      # the most recent captured scan's id


class ITargetRegistry( Protocol ):
    def targets( self ) -> tuple[ StorageTarget, ... ]: ...
    def resolve( self, words: str ) -> StorageTarget: ...           # StorageRefused("unknown_machine")


class IMachineTransport( Protocol ):
    """The helper's operations on one machine. Every call proves the machine's identity first."""

    def call( self, op: str, args: dict[ str, Any ], data: bytes | None = None ) -> dict: ...


class ITransportFactory( Protocol ):
    def for_target( self, target: StorageTarget ) -> IMachineTransport: ...


class IStorageAudit( Protocol ):
    def record( self, entry: AuditRecord ) -> None: ...
    def for_scan( self, scan_id: str ) -> list[ AuditRecord ]: ...
    def recent( self, limit: int ) -> list[ AuditRecord ]: ...


class IRunRecord( Protocol ):
    """The durable status of one run, rewritten at every phase so a long transfer can be watched."""

    def phase( self, phase_name: str, /, **facts: Any ) -> None: ...


class IPendingCopies( Protocol ):
    """A staged copy of an original kept on the Bridge machine while its destination is unreachable."""

    def keep( self, scan_id: str, machine: str, folder: str, meta: dict, data: bytes ) -> str: ...
    def drop( self, scan_id: str, machine: str ) -> None: ...
    def read( self, scan_id: str ) -> tuple[ bytes, dict ] | None: ...     # the bytes and the scanner's metadata
    def listed( self ) -> list[ dict ]: ...


class IScanStorage( Protocol ):
    def list_storage_targets( self ) -> StorageOutcome: ...
    def list_allowed_folders( self, machine: str ) -> StorageOutcome: ...
    def store_scan( self, scan_id: str, machine: str, folder: str, copy: bool = True ) -> StorageOutcome: ...
    def retrieve_scan( self, scan_id: str, machine: str ) -> StorageOutcome: ...
    def verify_scan( self, scan_id: str, machine: str, folder: str ) -> StorageOutcome: ...
    def storage_health( self, machine: str ) -> StorageOutcome: ...
    def transfer_status( self, scan_id: str ) -> StorageOutcome: ...
