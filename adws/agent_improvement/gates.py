"""The gate chain and the promotion policy. A candidate reaches neither: both are built from engine code.

The chain is the RSI framework's `GateChain` with its four gates, plus four that this engine needs:

    patch_policy     the candidate was produced by an allowed patch (IChangePolicy found no problem)
    judge_integrity  the judge, task catalog and gate list are byte for byte what phase 1 fingerprinted
    token_growth     the candidate's estimated tokens are within 10% of the baseline's (a labelled estimate)
    held_out         the candidate did not newly fail anything on the tasks the improver never saw

Order is behaviour: `GateChain` stops at the first BLOCK, so the structural gates go first.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from . import rsi

GATE_NAMES = ( "patch_policy", "judge_integrity", "safety", "cost", "token_growth",
               "regression", "held_out", "usefulness" )


def _result( name: str, ok: bool, reason: str, evidence: dict | None = None ) -> rsi.GateResult:
    return rsi.GateResult( gate_name=name, decision=rsi.GateDecision.ALLOW if ok else rsi.GateDecision.BLOCK,
                           reason=reason, evidence=evidence or {})


class PatchPolicyGate( rsi.IGate ):
    @property
    def name( self ) -> str:
        return "patch_policy"

    def check( self, context: dict ) -> rsi.GateResult:
        problems = list( context.get( "patch_problems", []))
        return _result( self.name, not problems and bool( context.get( "candidate_revision" )),
                        "; ".join( problems ) or "patch stays inside the permitted section",
                        { "problems": problems })


class JudgeIntegrityGate( rsi.IGate ):
    @property
    def name( self ) -> str:
        return "judge_integrity"

    def check( self, context: dict ) -> rsi.GateResult:
        then, now = context.get( "judge_fingerprint_recorded", "" ), context.get( "judge_fingerprint_now", "" )
        return _result( self.name, bool( then ) and then == now,
                        "judge unchanged since the cycle began" if then == now else "the judge changed during the cycle",
                        { "recorded": then[ :16 ], "now": now[ :16 ]})


class TokenGrowthGate( rsi.IGate ):
    def __init__( self, limit: float = 1.10 ):
        self._limit = limit

    @property
    def name( self ) -> str:
        return "token_growth"

    def check( self, context: dict ) -> rsi.GateResult:
        base, cand = int( context.get( "baseline_tokens", 0 )), int( context.get( "candidate_tokens", 0 ))
        if base <= 0:
            return _result( self.name, True, "no baseline token figure; not applied" )
        ok = cand <= base * self._limit
        return _result( self.name, ok, f"candidate { cand } vs baseline { base } estimated tokens (limit x{ self._limit:g})",
                        { "baseline": base, "candidate": cand, "source": context.get( "token_source", "" )})


class HeldOutGate( rsi.IGate ):
    @property
    def name( self ) -> str:
        return "held_out"

    def check( self, context: dict ) -> rsi.GateResult:
        regressions = list( context.get( "held_out_regressions", []))
        if not context.get( "held_out_ran" ):
            return _result( self.name, False, "the held-out variation did not run" )
        return _result( self.name, not regressions,
                        "; ".join( regressions ) or "no held-out check got worse",
                        { "regressions": regressions, "improvements": context.get( "held_out_improvements", [])})


def gate_chain() -> rsi.GateChain:
    return ( rsi.GateChain().add( PatchPolicyGate()).add( JudgeIntegrityGate()).add( rsi.SafetyGate())
             .add( rsi.CostGate()).add( TokenGrowthGate()).add( rsi.RegressionGate()).add( HeldOutGate())
             .add( rsi.UsefulnessGate()))


def fingerprint( sources: list[ Path ], catalog_json: str ) -> str:
    """sha256 over the judge's source files, the task catalog and the gate list."""
    digest = hashlib.sha256()
    for path in sorted( sources ):
        digest.update( path.name.encode())
        digest.update( path.read_bytes())
    digest.update( catalog_json.encode())
    digest.update( ",".join( GATE_NAMES ).encode())
    return digest.hexdigest()


class EnvironmentPromotionPolicy:
    """Implements IPromotionPolicy.

    `standing` names an approver recorded without a person, per environment, and is used only when every
    gate allowed. `people` names who may approve by hand. A live environment has no standing approver.
    """

    def __init__( self, standing: dict[ str, str ], people: dict[ str, frozenset[ str ]]):
        self._standing, self._people = dict( standing ), { k: frozenset( v ) for k, v in people.items()}

    def standing_approver( self, environment: str ) -> str:
        return self._standing.get( environment, "" )

    def may_approve( self, approver: str, environment: str ) -> tuple[ bool, str ]:
        if approver and approver == self._standing.get( environment ):
            return True, f"standing policy for the { environment } environment"
        if approver in self._people.get( environment, frozenset()):
            return True, f"{ approver } may approve in the { environment } environment"
        return False, f"{ approver or '(nobody)' } may not approve an activation in the { environment } environment"
