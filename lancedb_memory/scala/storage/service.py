"""ScanStorage: IScanStorage over a scan source, a target registry, transports, an audit trail, a run record and
staged pending copies -- every one injected.

A filing is always a COPY. The original stays in the scanner's store (it is only ever read), and Scala re-reads
its metadata after the transfer to prove it is still there and unchanged. The destination name is Scala's own
cabinet convention, `<scan date>_<folder leaf>_<first 12 of sha256>.<ext>`, so filing the same scan twice
lands on the same name: the second attempt is a verified duplicate, never a second copy and never an overwrite.
"""
from __future__ import annotations

import base64
import hashlib
import re
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from .interfaces import (IMachineTransport, IPendingCopies, IRunRecord, IScanSource, IStorageAudit, ITargetRegistry,
                         ITransportFactory, StorageRefused, TargetUnreachable)
from .models import AuditRecord, StorageOutcome, StorageTarget
from .targets import resolve_folder

SCAN_ID  = re.compile( r"^scan_[0-9A-HJKMNP-TV-Z]{26}$" )
SUFFIXES = { "image/jpeg": ".jpg", "image/png": ".png", "application/pdf": ".pdf" }
LATEST   = frozenset({ "last", "latest", "last scan", "the last scan" })
SOURCE   = "scanner API (SQMC dashboard scan façade)"


def filing_name( meta: dict, folder: str ) -> str:
    suffix = SUFFIXES.get( str( meta.get( "mime_type" )))
    if suffix is None:
        raise StorageRefused( "unsupported_type", f"Scala files JPEG, PNG and PDF scans, not { meta.get( 'mime_type' )}" )
    day = str( meta.get( "captured_at" ) or "" )[ :10 ]
    if not re.fullmatch( r"\d{4}-\d{2}-\d{2}", day ):
        raise StorageRefused( "no_capture_date", "the scan has no capture date" )
    return f"{ day }_{ folder.rsplit( '/', 1 )[ -1 ]}_{ str( meta[ 'sha256' ])[ :12 ]}{ suffix }"


class ScanStorage:
    def __init__( self, source: IScanSource, registry: ITargetRegistry, transports: ITransportFactory,
                  audit: IStorageAudit, run: IRunRecord, pending: IPendingCopies, retrieved_dir: Path,
                  adw_id: str = "", task_id: str = "", client: str = "",
                  now: Callable[[], datetime ] = lambda: datetime.now( timezone.utc )) -> None:
        self._source, self._registry, self._transports = source, registry, transports
        self._audit, self._run, self._pending, self._retrieved = audit, run, pending, retrieved_dir
        self._adw_id, self._task_id, self._client, self._now = adw_id, task_id, client, now

    # -- IScanStorage -----------------------------------------------------------

    def list_storage_targets( self ) -> StorageOutcome:
        targets = [ t.public() for t in self._registry.targets()]
        return StorageOutcome( ok=True, verified=True, line="storage machines: " + ", ".join(
            f"{ t[ 'machine' ]} ({ t[ 'hostname' ]})" for t in targets ), data={ "targets": targets })

    def list_allowed_folders( self, machine: str ) -> StorageOutcome:
        target = self._registry.resolve( machine )
        try:
            answer = self._transport( target ).call( "list", {})
        except TargetUnreachable as exc:
            return StorageOutcome( ok=False, retryable=True, line=str( exc ),
                                   data={ "machine": target.machine, "folders": [{ "folder": f } for f in target.folders ]})
        return StorageOutcome( ok=True, verified=True, line=f"{ target.machine } ({ answer[ 'hostname' ]}) approved folders under "
                               f"{ answer[ 'root' ]}: " + ", ".join( target.folders ),
                               data={ "machine": target.machine, "hostname": answer[ "hostname" ], "root": answer[ "root" ],
                                      "folders": answer[ "folders" ]})

    def storage_health( self, machine: str ) -> StorageOutcome:
        target = self._registry.resolve( machine )
        try:
            answer = self._transport( target ).call( "health", {})
        except TargetUnreachable as exc:
            return StorageOutcome( ok=False, retryable=True, line=str( exc ), data={ "machine": target.machine, "reachable": False })
        except StorageRefused as exc:
            return StorageOutcome( ok=False, line=f"{ target.machine }: { exc.message }",
                                   data={ "machine": target.machine, "reachable": True, "error": exc.code, **exc.extra })
        healthy = bool( answer.get( "writable" ))
        return StorageOutcome( ok=healthy, verified=healthy,
                               line=f"{ target.machine } is { answer[ 'hostname' ]}; { answer[ 'root' ]} is "
                                    f"{ 'writable' if healthy else 'NOT writable' }, { answer[ 'free_bytes' ] // 2**30 } GiB free",
                               data={ "machine": target.machine, "reachable": True, **{ k: v for k, v in answer.items() if k != "ok" }})

    def store_scan( self, scan_id: str, machine: str, folder: str, copy: bool = True ) -> StorageOutcome:
        if not copy:
            raise StorageRefused( "move_refused", "Scala only copies scans; the original is always preserved" )
        target = self._registry.resolve( machine )
        approved = resolve_folder( target, folder )
        scan_id = self._scan_id( scan_id )
        self._run.phase( "resolved", scan_id=scan_id, machine=target.machine, folder=approved )
        meta, data, from_pending = self._original( scan_id )
        name = filing_name( meta, approved )
        self._run.phase( "fetched", size=len( data ), sha256=meta[ "sha256" ], name=name, from_pending=from_pending )
        entry = dict( operation="store", scan_id=scan_id, source_sha256=meta[ "sha256" ], machine=target.machine,
                      folder=approved )
        try:
            self._run.phase( "transferring", machine=target.machine )
            stored = self._transport( target ).call( "put", { "folder": approved, "name": name, "sha256": meta[ "sha256" ],
                                                              "size": len( data )}, data )
            self._run.phase( "verifying" )
            check = self._transport( target ).call( "verify", { "folder": approved, "name": name })
        except TargetUnreachable as exc:
            kept = self._pending.keep( scan_id, target.machine, approved, meta, data )
            self._log( entry, result="unreachable", detail=str( exc ))
            self._run.phase( "unreachable", pending_copy=kept )
            return StorageOutcome( ok=False, retryable=True,
                                   line=f"{ target.machine } is unreachable; nothing was filed. The original is preserved in the "
                                        f"scanner and a staged copy is kept for retry ({ exc })",
                                   data={ "scan_id": scan_id, "machine": target.machine, "folder": approved, "pending_copy": kept,
                                          "original_preserved": True })
        except StorageRefused as exc:
            self._log( entry, hostname=str( exc.extra.get( "hostname", "" )), result=f"refused:{ exc.code }", detail=exc.message )
            self._run.phase( "refused", error=exc.code )
            raise
        verified = ( check[ "sha256" ] == meta[ "sha256" ] and check[ "size" ] == len( data ) == meta[ "size_bytes" ]
                     and check[ "hostname" ].lower() == target.hostname.lower())
        preserved = self._preserved( scan_id, meta )
        self._log( entry, hostname=check[ "hostname" ], destination=check[ "path" ], sha256=check[ "sha256" ],
                   size=check[ "size" ], result=stored[ "status" ] if verified else "failed:verify_mismatch",
                   detail="" if preserved else "the original could not be re-read after the copy" )
        if verified:
            self._pending.drop( scan_id, target.machine )
        self._run.phase( "done" if verified and preserved else "failed", status=stored[ "status" ], path=check[ "path" ])
        word = "already there (same bytes)" if stored[ "status" ] == "duplicate" else "copied"
        return StorageOutcome( ok=verified and preserved, verified=verified and preserved,
                               line=f"{ scan_id } { word } to { target.machine } ({ check[ 'hostname' ]}) { check[ 'path' ]}, "
                                    f"{ check[ 'size' ]} bytes, sha256 { check[ 'sha256' ]}"
                                    + ( "; original preserved" if preserved else "; ORIGINAL NOT CONFIRMED" ),
                               data={ "scan_id": scan_id, "machine": target.machine, "hostname": check[ "hostname" ],
                                      "folder": approved, "filename": name, "destination": check[ "path" ],
                                      "size": check[ "size" ], "sha256": check[ "sha256" ], "source_sha256": meta[ "sha256" ],
                                      "status": stored[ "status" ], "original_preserved": preserved })

    def verify_scan( self, scan_id: str, machine: str, folder: str ) -> StorageOutcome:
        target = self._registry.resolve( machine )
        approved = resolve_folder( target, folder )
        scan_id = self._scan_id( scan_id )
        meta = self._source.document( scan_id )
        name = filing_name( meta, approved )
        try:
            check = self._transport( target ).call( "verify", { "folder": approved, "name": name })
        except TargetUnreachable as exc:
            return StorageOutcome( ok=False, retryable=True, line=str( exc ), data={ "scan_id": scan_id, "machine": target.machine })
        except StorageRefused as exc:
            return StorageOutcome( ok=False, line=f"{ scan_id } is not verified on { target.machine }: { exc.message }",
                                   data={ "scan_id": scan_id, "machine": target.machine, "error": exc.code })
        good = check[ "sha256" ] == meta[ "sha256" ] and check[ "size" ] == meta[ "size_bytes" ]
        return StorageOutcome( ok=good, verified=good,
                               line=f"{ scan_id } on { target.machine } ({ check[ 'hostname' ]}) { check[ 'path' ]}: "
                                    + ( "matches the original" if good else "DOES NOT match the original" ),
                               data={ "scan_id": scan_id, "machine": target.machine, **{ k: v for k, v in check.items() if k != "ok" },
                                      "expected_sha256": meta[ "sha256" ], "expected_size": meta[ "size_bytes" ]})

    def retrieve_scan( self, scan_id: str, machine: str ) -> StorageOutcome:
        target = self._registry.resolve( machine )
        scan_id = self._scan_id( scan_id )
        where = self._located( scan_id, target )
        try:
            got = self._transport( target ).call( "read", where )
        except TargetUnreachable as exc:
            return StorageOutcome( ok=False, retryable=True, line=str( exc ), data={ "scan_id": scan_id, "machine": target.machine })
        data = base64.b64decode( got[ "content_b64" ])
        good = hashlib.sha256( data ).hexdigest() == got[ "sha256" ] and len( data ) == got[ "size" ]
        out = self._retrieved / where[ "name" ]
        self._retrieved.mkdir( parents=True, exist_ok=True )
        if good and not out.exists():
            out.write_bytes( data )
        return StorageOutcome( ok=good, verified=good,
                               line=f"retrieved { scan_id } from { target.machine } ({ got[ 'hostname' ]}) { got[ 'path' ]}, "
                                    f"{ got[ 'size' ]} bytes, sha256 { got[ 'sha256' ]}",
                               data={ "scan_id": scan_id, "machine": target.machine, "hostname": got[ "hostname" ],
                                      "stored_at": got[ "path" ], "folder": where[ "folder" ], "filename": where[ "name" ],
                                      "size": got[ "size" ], "sha256": got[ "sha256" ], "local_copy": str( out ) if good else "" })

    def transfer_status( self, scan_id: str ) -> StorageOutcome:
        rows = self._audit.for_scan( self._scan_id( scan_id )) if scan_id else self._audit.recent( 20 )
        pending = [ p for p in self._pending.listed() if not scan_id or p.get( "scan_id" ) == scan_id ]
        last = rows[ -1 ] if rows else None
        line = ( f"{ len( rows )} filing attempt(s); last: { last.result } on { last.machine } { last.destination }".rstrip()
                 if last else "no filing attempts recorded" ) + ( f"; { len( pending )} waiting for retry" if pending else "" )
        return StorageOutcome( ok=True, verified=True, line=line,
                               data={ "attempts": [ r.model_dump( mode="json" ) for r in rows ], "pending": pending })

    # -- helpers ----------------------------------------------------------------

    def _transport( self, target: StorageTarget ) -> IMachineTransport:
        return self._transports.for_target( target )

    def _scan_id( self, scan_id: str ) -> str:
        text = scan_id.strip()
        if text.lower() in LATEST:
            return self._source.latest()
        if not SCAN_ID.match( text ):
            raise StorageRefused( "invalid_scan_id", f"{ scan_id!r} is not a scan id (scan_ + 26 characters, or 'last')" )
        return text

    def _original( self, scan_id: str ) -> tuple[ dict, bytes, bool ]:
        """The scanner's metadata and original bytes, checked against each other. A staged copy is used only
        when the scanner itself cannot be reached, and only if it hashes to what was recorded."""
        try:
            meta = self._source.document( scan_id )
            data = self._source.original( scan_id )
            from_pending = False
        except StorageRefused as exc:
            staged = self._pending.read( scan_id ) if exc.code == "scanner_unreachable" else None
            if staged is None:
                raise
            meta, data, from_pending = staged[ 1 ], staged[ 0 ], True
        if hashlib.sha256( data ).hexdigest() != meta.get( "sha256" ) or len( data ) != meta.get( "size_bytes" ):
            raise StorageRefused( "hash_mismatch", "the scanner's original does not match its own recorded size and SHA-256" )
        return meta, data, from_pending

    def _preserved( self, scan_id: str, meta: dict ) -> bool:
        try:
            again = self._source.document( scan_id )
        except StorageRefused:
            return False
        return again.get( "sha256" ) == meta[ "sha256" ] and again.get( "size_bytes" ) == meta[ "size_bytes" ]

    def _located( self, scan_id: str, target: StorageTarget ) -> dict:
        for row in reversed( self._audit.for_scan( scan_id )):
            if row.machine == target.machine and row.result in ( "stored", "duplicate" ) and row.destination:
                return { "folder": row.folder, "name": Path( row.destination ).name }
        sha = self._source.document( scan_id )[ "sha256" ]
        try:
            found = self._transport( target ).call( "find", { "sha12": sha[ :12 ]})[ "found" ]
        except TargetUnreachable as exc:
            raise StorageRefused( "unreachable", str( exc )) from None
        if not found:
            raise StorageRefused( "not_found", f"{ scan_id } is not filed on { target.machine }" )
        return found[ 0 ]

    def _log( self, entry: dict, **more: object ) -> None:
        self._audit.record( AuditRecord( at=self._now(), adw_id=self._adw_id, task_id=self._task_id, client=self._client,
                                         source=SOURCE, **entry, **more ))
