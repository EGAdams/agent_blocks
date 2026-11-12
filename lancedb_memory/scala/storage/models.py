"""Scala's storage records. Pydantic, because they cross the ADW boundary and land in the audit trail."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SshRoute( BaseModel ):
    model_config = ConfigDict( extra="forbid", frozen=True )

    host: str = Field( pattern=r"^[A-Za-z0-9.:-]{1,80}$" )
    port: int = Field( ge=1, le=65535 )
    user: str = Field( pattern=r"^[a-z_][a-z0-9_-]{0,31}$" )


class StorageTarget( BaseModel ):
    """One machine Scala may file on. Data, not code: it comes from storage_targets.json."""

    model_config = ConfigDict( extra="forbid", frozen=True )

    machine:     str                            # BATI, R46, SQMC
    hostname:    str                            # what the machine must report before anything is written
    description: str = ""
    aliases:     tuple[ str, ... ] = ()
    root:        str
    ssh:         SshRoute | None = None
    folders:     tuple[ str, ... ]

    def public( self ) -> dict[ str, Any ]:
        return { "machine": self.machine, "hostname": self.hostname, "description": self.description,
                 "aliases": list( self.aliases ), "root": self.root, "folders": list( self.folders )}


class StorageOutcome( BaseModel ):
    """What one ADW run answers: the line a person reads, and the facts behind it."""

    model_config = ConfigDict( extra="forbid" )

    ok:        bool
    verified:  bool = False
    line:      str
    retryable: bool = False
    data:      dict[ str, Any ] = {}


class AuditRecord( BaseModel ):
    """One line of the audit trail per filing attempt, whatever happened."""

    model_config = ConfigDict( extra="forbid" )

    at:               datetime
    adw_id:           str = ""
    task_id:          str = ""
    client:           str = ""
    operation:        str
    scan_id:          str
    source:           str
    source_sha256:    str = ""
    machine:          str
    hostname:         str = ""
    folder:           str = ""
    destination:      str = ""
    sha256:           str = ""
    size:             int | None = None
    result:           str                       # stored | duplicate | unreachable | refused:<code> | failed:<code>
    detail:           str = ""
