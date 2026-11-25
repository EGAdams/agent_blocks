"""Merge failures into the cluster ledger. Pure: no I/O, the clock is a parameter.

The failed mailboxes are never emptied, so every pass sees the whole history. Counts are therefore
recomputed from scratch each pass, which makes the merge idempotent by construction: a crash, a rerun or
a pass that reads the same files twice changes nothing.

States: open -> routed -> fixed -> closed. Any cluster closes once it has been quiet for `quiet_days`
(since its last failure, and since its fix if it has one). A failure after a recorded fix is a
regression; a failure after a close with no fix reopens it.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .interfaces import IFingerprinter, IOwnerTable
from .models import Cluster, ClusterChange, Failure

KEEP_TASKS    = 20
KEEP_VARIANTS = 10
MAIL_AGAIN    = timedelta( days=1 )
EG_CLASSES    = { "environment", "person" }


def reconcile( clusters: dict[ str, Cluster ], failures: list[ Failure ], fingerprinter: IFingerprinter,
               owners: IOwnerTable, now: datetime, quiet_days: float = 3, repeat_at: int = 2
               ) -> list[ ClusterChange ]:
    quiet = timedelta( days=quiet_days )
    groups: dict[ str, list[ Failure ]] = {}
    for failure in failures:
        groups.setdefault( fingerprinter.fingerprint( failure.agent, failure.error ), []).append( failure )

    changes: list[ ClusterChange ] = []
    for fp, group in groups.items():
        group.sort( key=lambda f: f.failed_at )
        newest = group[ -1 ]
        cluster = clusters.get( fp )
        if cluster is None:
            rule = owners.match( newest.agent, fingerprinter.normalise( newest.error ))
            cluster = Cluster( id=fp, agent=newest.agent, sample_error=newest.error, first_seen=group[ 0 ].failed_at,
                               last_seen=newest.failed_at, klass=rule.klass, rule=rule.name, owner=rule.owner )
            clusters[ fp ] = cluster
            changes.append( ClusterChange( cluster=fp, kind="new", added=len( group )))
            recent = now - cluster.last_seen < quiet
            if recent and ( cluster.klass in EG_CLASSES or len( group ) >= repeat_at ):
                _owe_mail( cluster, now, "needs you" if cluster.klass in EG_CLASSES else "keeps happening" )
        else:
            added = len( group ) - cluster.count
            later = [ f for f in group if f.failed_at > cluster.last_seen ]
            if cluster.state in ( "fixed", "closed" ) and later and _after_fix( cluster, later ):
                if cluster.fix:
                    cluster.state, cluster.regressions = "open", cluster.regressions + 1
                    cluster.routed_task, cluster.route_error = "", ""
                    changes.append( ClusterChange( cluster=fp, kind="regressed", added=len( later )))
                    _owe_mail( cluster, now, "regressed after its fix", force=True )
                else:
                    cluster.state = "routed" if cluster.routed_task else "open"
                    changes.append( ClusterChange( cluster=fp, kind="reopened", added=len( later )))
                    _owe_mail( cluster, now, "came back" )
                cluster.closed_at = None
            elif added > 0:
                changes.append( ClusterChange( cluster=fp, kind="grown", added=added ))
                if cluster.count < repeat_at <= len( group ):
                    _owe_mail( cluster, now, "keeps happening" )
            cluster.last_seen = max( cluster.last_seen, newest.failed_at )
            cluster.sample_error = newest.error
        cluster.count = len( group )
        cluster.task_ids = [ f.task_id for f in group ][ -KEEP_TASKS: ]
        cluster.variants = list( dict.fromkeys( f.error for f in reversed( group )))[ :KEEP_VARIANTS ]
        cluster.sample_request = newest.request

    for fp, cluster in clusters.items():
        if cluster.state == "closed":
            continue
        since = max( cluster.last_seen, cluster.fixed_at or cluster.last_seen )
        if now - since >= quiet:
            cluster.state, cluster.closed_at, cluster.mail_reason = "closed", now, ""
            changes.append( ClusterChange( cluster=fp, kind="closed" ))
    return changes


def mark_fixed( cluster: Cluster, fix: str, now: datetime ) -> None:
    cluster.fix, cluster.fixed_at = fix.strip(), now
    if cluster.state != "closed":
        cluster.state = "fixed"


def mark_routed( cluster: Cluster, task_id: str ) -> None:
    cluster.routed_task, cluster.route_error = task_id, ""
    if cluster.state == "open":
        cluster.state = "routed"


def counts( clusters: dict[ str, Cluster ]) -> dict[ str, int ]:
    out = { "open": 0, "routed": 0, "fixed": 0, "closed": 0 }
    for cluster in clusters.values():
        out[ cluster.state ] += 1
    out[ "live" ] = out[ "open" ] + out[ "routed" ] + out[ "fixed" ]
    return out


def _after_fix( cluster: Cluster, later: list[ Failure ]) -> bool:
    if cluster.fixed_at is None:
        return True
    return any( f.failed_at > cluster.fixed_at for f in later )


def _owe_mail( cluster: Cluster, now: datetime, reason: str, force: bool = False ) -> None:
    if not force and cluster.mailed_at is not None and now - cluster.mailed_at < MAIL_AGAIN:
        return
    cluster.mail_reason = reason
