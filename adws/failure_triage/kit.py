"""The only module that names Failure Triage's concretes."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ADWS      = Path( __file__ ).resolve().parents[ 1 ]
WORKSPACE = ADWS.parent
LANCEDB   = WORKSPACE / "lancedb_memory"
if str( LANCEDB ) not in sys.path:
    sys.path.insert( 0, str( LANCEDB ))

from .corrections import CaseHistoryCorrections   # noqa: E402
from .fingerprint import RuleFingerprinter          # noqa: E402
from .ledger import JsonClusterLedger               # noqa: E402
from .owners import JsonOwnerTable                  # noqa: E402
from .router import PostOfficeRouter, YolandaNotifier, default_ops   # noqa: E402
from .sources import FailedMailboxSource            # noqa: E402
from .status import TriageDesk                      # noqa: E402
from .triage import TriagePass                      # noqa: E402

DATA = Path( os.getenv( "FAILURE_TRIAGE_DATA" ) or ADWS / "adw_data" / "failure_triage" )


def post_office_root() -> Path:
    return Path( os.getenv( "AGENT_POST_OFFICE_ROOT" ) or ADWS / "adw_data" / "post_office" )


def _secrets():
    from agent_bridge_worker.redaction import SecretScanner
    return SecretScanner([])


def build_pass( dry_run: bool = False, quiet_days: float = 3, repeat_at: int = 2, data: Path | None = None ) -> TriagePass:
    root = post_office_root()
    secrets = _secrets()
    return TriagePass( sources=[ FailedMailboxSource( root / "mailboxes" )], fingerprinter=RuleFingerprinter(),
                       ledger=JsonClusterLedger(( data or DATA ) / "clusters.json" ), owners=JsonOwnerTable(),
                       router=PostOfficeRouter( default_ops, secrets ), notifier=YolandaNotifier( default_ops, secrets ),
                       quiet_days=quiet_days, repeat_at=repeat_at, dry_run=dry_run )


def build_desk( data: Path | None = None ) -> TriageDesk:
    return TriageDesk( JsonClusterLedger(( data or DATA ) / "clusters.json" ),
                       CaseHistoryCorrections( post_office_root() / "mechanic_cases.jsonl" ))
