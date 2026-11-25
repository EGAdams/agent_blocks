"""ICorrectionLog over the mechanic's case history (`mechanic_cases.jsonl`).

One case line per distinct raw error in the cluster, each carrying the fix as its `correction`, in the
exact shape `agent_comms.mechanic.JsonlCaseHistory.record` writes. That history's `similar()` puts cases
with a correction first and names the fix, so the next diagnosis of the same error says how it was fixed.
Append-only: no existing line is changed.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from .models import Cluster


class CaseHistoryCorrections:
    def __init__( self, path: Path ) -> None:
        self._path = path

    def record( self, cluster: Cluster, correction: str ) -> str:
        ids: list[ str ] = []
        self._path.parent.mkdir( parents=True, exist_ok=True )
        with open( self._path, "a", encoding="utf-8" ) as handle:
            for error in cluster.variants or [ cluster.sample_error ]:
                case_id = f"case-{ uuid.uuid4().hex[ :8 ] }"
                handle.write( json.dumps({ "case_id": case_id, "task_id": cluster.task_ids[ -1 ] if cluster.task_ids else "",
                                           "recipient": cluster.agent, "error": error, "failed_phase": None,
                                           "correction": correction }) + "\n" )
                ids.append( case_id )
        return ", ".join( ids )
