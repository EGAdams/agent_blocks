"""One triage pass as plain methods, so the ADW's phases stay thin and the whole thing can later run
without an ADW: `TriagePass( ... ).collect(); .reconcile(); .route(); .notify(); .report()`.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from .interfaces import IClusterLedger, IFailureSource, IFingerprinter, INotifier, IOwnerTable, ITriageRouter, RouteRefused
from .models import Cluster, Failure, PassReport
from .reconcile import counts, mark_routed, reconcile

DIGEST_LIMIT = 12


def utcnow() -> datetime:
    return datetime.now( timezone.utc )


class TriagePass:

    def __init__( self, sources: list[ IFailureSource ], fingerprinter: IFingerprinter, ledger: IClusterLedger,
                  owners: IOwnerTable, router: ITriageRouter, notifier: INotifier, quiet_days: float = 3,
                  repeat_at: int = 2, dry_run: bool = False, clock: Callable[ [], datetime ] = utcnow ) -> None:
        self._sources, self._fp, self._ledger, self._owners = sources, fingerprinter, ledger, owners
        self._router, self._notifier = router, notifier
        self._quiet, self._repeat, self._dry, self._clock = quiet_days, repeat_at, dry_run, clock
        self.failures: list[ Failure ] = []
        self.clusters: dict[ str, Cluster ] = {}
        self.result = PassReport( ok=True, line="", dry_run=dry_run )

    def _advice( self, cluster: Cluster ) -> str:
        rule = self._owners.rule( cluster.rule )
        return rule.advice if rule else ""

    # -- phases ------------------------------------------------------------------------------------

    def collect( self ) -> int:
        self.clusters = self._ledger.load()
        for source in self._sources:
            try:
                self.failures.extend( source.read())
            except OSError as exc:
                self.result.unreadable.append( f"{ source.name }: { exc }" )
            self.result.unreadable.extend( getattr( source, "unreadable", []))
        self.result.read = len( self.failures )
        return self.result.read

    def reconcile( self ) -> int:
        self.result.changes = reconcile( self.clusters, self.failures, self._fp, self._owners, self._clock(),
                                         self._quiet, self._repeat )
        return len( self.result.changes )

    def route( self ) -> int:
        for cluster in self.clusters.values():
            if not cluster.needs_owner() or cluster.owner in ( "eg", "sender" ):
                continue
            rule = self._owners.rule( cluster.rule )
            rule = rule or self._owners.match( cluster.agent, self._fp.normalise( cluster.sample_error ))
            if self._dry:
                self.result.routed[ cluster.id ] = f"(dry run) { rule.task_type } -> { rule.owner }"
                continue
            try:
                mark_routed( cluster, self._router.send( cluster, rule ))
                self.result.routed[ cluster.id ] = cluster.routed_task
            except RouteRefused as exc:
                cluster.route_error = str( exc )[ :300 ]
                cluster.mail_reason = cluster.mail_reason or "could not be routed"
                self.result.route_errors[ cluster.id ] = cluster.route_error
        return len( self.result.routed )

    def notify( self ) -> str:
        owed = sorted(( c for c in self.clusters.values() if c.mail_reason ), key=lambda c: -c.count )
        if not owed:
            return ""
        subject, body = digest( owed[ :DIGEST_LIMIT ], len( owed ), counts( self.clusters ), self._advice )
        if self._dry:
            self.result.mailed = f"(dry run) { subject }"
            return self.result.mailed
        try:
            self.result.mailed = self._notifier.digest( subject, body ) or "(sent)"
        except ( RouteRefused, RuntimeError ) as exc:   # mail_reason stays set, so the next pass tries again
            self.result.route_errors[ "mail" ] = str( exc )[ :300 ]
            return ""
        now = self._clock()
        for cluster in owed[ :DIGEST_LIMIT ]:
            cluster.mailed_at, cluster.mail_reason = now, ""
        return self.result.mailed

    def report( self ) -> PassReport:
        if not self._dry:
            self._ledger.save( self.clusters )
        c = counts( self.clusters )
        self.result.counts = c
        changed = { k: sum( 1 for x in self.result.changes if x.kind == k ) for k in ( "new", "grown", "regressed", "reopened", "closed" )}
        bits = [ f"{ n } { k }" for k, n in changed.items() if n ]
        self.result.line = ( f"{ 'DRY RUN: ' if self._dry else 'DONE: ' }{ c[ 'live' ] } live cluster(s) "
                             f"({ c[ 'open' ] } open, { c[ 'routed' ] } routed, { c[ 'fixed' ] } fixed), { c[ 'closed' ] } closed"
                             f"{ '; this pass: ' + ', '.join( bits ) if bits else '' }"
                             f"{ f'; routed { len( self.result.routed )}' if self.result.routed else '' }"
                             f"{ '; mailed EG' if self.result.mailed else '' }"
                             f"{ f'; { len( self.result.route_errors )} refused' if self.result.route_errors else '' }." )
        self.result.ok = not self.result.route_errors
        return self.result


def digest( owed: list[ Cluster ], total: int, c: dict[ str, int ],
            advice: Callable[ [ Cluster ], str ] = lambda _: "" ) -> tuple[ str, str ]:
    regressed = sum( 1 for x in owed if x.mail_reason.startswith( "regressed" ))
    subject = f"[Triage] { total } failure cluster(s) need attention" + ( f", { regressed } regression(s)" if regressed else "" )
    lines = [ f"Failure triage on SQMC: { c[ 'live' ] } live cluster(s), { c[ 'closed' ] } closed.", "" ]
    for cluster in owed:
        who = ( "you: " + ( advice( cluster ) or "see the error" )) if cluster.owner == "eg" else (
            f"the caller ({ cluster.owner })" if cluster.owner == "sender" else
            f"{ cluster.owner }, task { cluster.routed_task or '(not routed: ' + cluster.route_error + ')' }" )
        lines += [ f"* { cluster.agent } -- { cluster.mail_reason } [{ cluster.id }]",
                   f"  { cluster.count } time(s), last { cluster.last_seen:%Y-%m-%d %H:%M } UTC: { cluster.sample_error[ :200 ] }",
                   f"  With: { who }", "" ]
    if total > len( owed ):
        lines.append( f"...and { total - len( owed )} more; run adws/adw_failure_triage_status.py on SQMC." )
    lines.append( "Record a fix: adws/adw_failure_triage_fix.py --cluster <id> --fix \"<commit> <what>\"" )
    return subject, "\n".join( lines )
