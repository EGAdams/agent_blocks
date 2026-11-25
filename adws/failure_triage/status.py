"""Read and change the ledger outside a pass: status, one cluster, record a fix, re-route, close.

`TriageDesk` is what the status and fix ADWs and the Bridge agent call. Reads never write; each write is
one cluster and one ledger save.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from .interfaces import IClusterLedger, ICorrectionLog
from .models import Cluster
from .reconcile import counts, mark_fixed
from .triage import utcnow


class UnknownCluster( LookupError ):
    pass


def brief( cluster: Cluster ) -> dict[ str, Any ]:
    return { "id": cluster.id, "agent": cluster.agent, "state": cluster.state, "klass": cluster.klass,
             "owner": cluster.owner, "count": cluster.count, "last_seen": cluster.last_seen.isoformat(),
             "error": cluster.sample_error[ :240 ], "routed_task": cluster.routed_task, "fix": cluster.fix }


class TriageDesk:

    def __init__( self, ledger: IClusterLedger, corrections: ICorrectionLog,
                  clock: Callable[ [], datetime ] = utcnow ) -> None:
        self._ledger, self._corrections, self._clock = ledger, corrections, clock

    def status( self ) -> tuple[ str, dict[ str, Any ]]:
        clusters = self._ledger.load()
        c = counts( clusters )
        live = sorted(( x for x in clusters.values() if x.is_live()), key=lambda x: ( -x.count, x.id ))
        line = ( f"{ c[ 'live' ] } live failure cluster(s): { c[ 'open' ] } open, { c[ 'routed' ] } routed, "
                 f"{ c[ 'fixed' ] } fixed awaiting a quiet spell; { c[ 'closed' ] } closed." )
        if live:
            line += " Largest: " + "; ".join( f"{ x.agent } x{ x.count } ({ x.state })" for x in live[ :3 ]) + "."
        return line, { "counts": c, "live": [ brief( x ) for x in live ]}

    def clusters( self, state: str = "" ) -> tuple[ str, dict[ str, Any ]]:
        chosen = [ x for x in self._ledger.load().values() if not state or x.state == state ]
        chosen.sort( key=lambda x: ( x.state == "closed", -x.count ))
        return f"{ len( chosen )} cluster(s){ ' in state ' + state if state else '' }.", { "clusters": [ brief( x ) for x in chosen ]}

    def cluster( self, cluster_id: str ) -> tuple[ str, dict[ str, Any ]]:
        found = self._find( self._ledger.load(), cluster_id )
        return ( f"{ found.agent } x{ found.count }, { found.state } ({ found.klass }, owner { found.owner }): "
                 f"{ found.sample_error[ :200 ] }" ), { "cluster": found.model_dump( mode="json" )}

    def record_fix( self, cluster_id: str, fix: str ) -> tuple[ str, dict[ str, Any ]]:
        if len( fix.strip()) < 5:
            raise ValueError( "a fix needs a few words: the commit and what it changed" )
        clusters = self._ledger.load()
        found = self._find( clusters, cluster_id )
        mark_fixed( found, fix, self._clock())
        cases = self._corrections.record( found, found.fix )
        self._ledger.save( clusters )
        return ( f"Recorded the fix for { found.id } ({ found.agent }); it closes after a quiet spell, and reopens as "
                 f"a regression if it fails again. Mechanic cases: { cases }." ), { "cluster": brief( found ), "cases": cases }

    def reroute( self, cluster_id: str ) -> tuple[ str, dict[ str, Any ]]:
        clusters = self._ledger.load()
        found = self._find( clusters, cluster_id )
        found.routed_task, found.route_error = "", ""
        if found.state == "routed":
            found.state = "open"
        self._ledger.save( clusters )
        return f"{ found.id } will be routed to { found.owner } again on the next pass.", { "cluster": brief( found )}

    @staticmethod
    def _find( clusters: dict[ str, Cluster ], cluster_id: str ) -> Cluster:
        key = cluster_id.strip()
        matches = [ c for k, c in clusters.items() if k.startswith( key )] if len( key ) >= 4 else []
        if len( matches ) != 1:
            raise UnknownCluster( f"no single cluster matches { cluster_id!r }" )
        return matches[ 0 ]
