"""Self-healing, in the only order that is safe:

  1. diagnose  -- evidence about the failed step; the page is not touched
  2. propose   -- one replacement locator, only if one element clearly
                  resembles the recorded one more than any other
  3. verify    -- the proposal locates exactly one usable element; it is
                  never clicked
  4. store     -- as a proposal, apart from the step
  5. apply     -- later, by a person who approves it, and only if verified

Nothing here performs an action with a proposed locator.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..interfaces.browser import IBrowserAdapter
from ..interfaces.locating import IElementResolver
from ..interfaces.playback import IFailureDiagnoser, IRepairProposer, IRepairVerifier, ISimilarityRanker
from ..interfaces.storage import ILocatorRepairStore, IRecordingRepository
from ..models import ElementSummary, LocatorCandidate, RecordingStep, RepairProposal, Resolution, StepDiagnostics

WORD = re.compile( r"[a-z0-9]+" )


def _words( *texts: str ) -> set[ str ]:
    return { w for t in texts for w in WORD.findall(( t or "" ).casefold()) if len( w ) > 1 }


class TokenOverlapRanker:
    """Share of the recorded element's words (text, labels, names) that an
    element on today's page also carries, plus a little for the same tag."""

    def rank( self, step: RecordingStep, elements: list[ ElementSummary ]) -> list[ tuple[ float, ElementSummary ]]:
        hints  = step.metadata.get( "field_hints" ) or {}
        wanted = _words( str( step.metadata.get( "text", "" )), *map( str, hints.values()),
                         *( c.value for c in step.locators if c.strategy in ( "text", "label" )))
        tag    = str( step.metadata.get( "tag", "" )).lower()
        scored = []
        for element in elements:
            have  = _words( element.text, *element.attrs.values())
            score = ( len( wanted & have ) / len( wanted )) if wanted else 0.0
            if tag and element.tag.lower() == tag:
                score += 0.1
            scored.append(( round( score, 3 ), element ))
        return sorted( scored, key=lambda pair: -pair[ 0 ])


class PageFailureDiagnoser:
    def __init__( self, evidence_dir: Path, ranker: ISimilarityRanker, keep: int = 5 ) -> None:
        self._dir    = Path( evidence_dir )
        self._ranker = ranker
        self._keep   = keep

    def diagnose( self, browser: IBrowserAdapter, step: RecordingStep, resolution: Resolution | None,
                  error: str, run_id: int ) -> StepDiagnostics:
        evidence = StepDiagnostics( error=error, attempts=resolution.attempts if resolution else [])
        try:
            evidence.url, evidence.title = browser.current_url, browser.title
        except Exception:
            pass
        try:
            self._dir.mkdir( parents=True, exist_ok=True )
            shot = self._dir / f"run_{ run_id }_step_{ step.position }.png"
            if browser.screenshot( str( shot )):
                evidence.screenshot = str( shot )
        except Exception:
            pass
        if step.locators:
            try:
                ranked = self._ranker.rank( step, browser.interactive_elements())
                evidence.similar = [ e for score, e in ranked[ :self._keep ] if score > 0 ]
            except Exception:
                pass
        return evidence


class SimilarElementRepairProposer:
    """Proposes the best-resembling element only when it is a clear winner."""

    def __init__( self, ranker: ISimilarityRanker, min_score: float = 0.5, margin: float = 0.15 ) -> None:
        self._ranker    = ranker
        self._min_score = min_score
        self._margin    = margin

    def propose( self, step: RecordingStep, diagnostics: StepDiagnostics ) -> RepairProposal | None:
        ranked = self._ranker.rank( step, diagnostics.similar )
        if not ranked or ranked[ 0 ][ 0 ] < self._min_score:
            return None
        if len( ranked ) > 1 and ranked[ 0 ][ 0 ] - ranked[ 1 ][ 0 ] < self._margin:
            return None                         # two look alike: a person must choose
        score, best = ranked[ 0 ]
        return RepairProposal( step_id=step.id, reason=f"resembles the recorded element (score { score:g}): "
                                                       f"<{ best.tag }> { best.text[ :60 ]!r}",
                               candidate=LocatorCandidate( strategy="css", value=best.css, source="repair", rank=5 ))


class LocatingRepairVerifier:
    def __init__( self, resolver: IElementResolver ) -> None:
        self._resolver = resolver

    def verify( self, browser: IBrowserAdapter, proposal: RepairProposal ) -> tuple[ bool, str ]:
        element, resolution = self._resolver.resolve( browser, [ proposal.candidate ])
        if element is not None:
            return True, f"finds exactly one usable <{ element.tag_name }>"
        outcome = resolution.attempts[ 0 ].outcome if resolution.attempts else "none"
        return False, f"does not hold up on the live page ({ outcome })"


class ProposingRecovery:
    """IPlaybackRecovery: diagnose, then propose-verify-store. Never applies."""

    def __init__( self, diagnoser: IFailureDiagnoser, proposer: IRepairProposer, verifier: IRepairVerifier,
                  store: ILocatorRepairStore ) -> None:
        self._diagnoser = diagnoser
        self._proposer  = proposer
        self._verifier  = verifier
        self._store     = store

    def recover( self, browser: IBrowserAdapter, step: RecordingStep, resolution: Resolution | None,
                 error: str, run_id: int ) -> StepDiagnostics:
        evidence = self._diagnoser.diagnose( browser, step, resolution, error, run_id )
        if not step.locators or ( resolution and resolution.found ):
            return evidence                     # the element was found; a new locator would not help
        proposal = self._proposer.propose( step, evidence )
        if proposal is None:
            evidence.repair_note = "no clear replacement element; edit the step's locators by hand"
            return evidence
        verified, why = self._verifier.verify( browser, proposal )
        stored = self._store.propose( proposal.model_copy( update={ "run_id": run_id, "verified": verified,
                                                                    "reason": f"{ proposal.reason }; { why }" }))
        evidence.repair_id   = stored.id
        evidence.repair_note = ( f"repair #{ stored.id } proposed ({ proposal.candidate.value }), { why }; "
                                 f"apply it with adw_browser_repair_locator.py --apply { stored.id }" if verified else
                                 f"repair #{ stored.id } was proposed but { why }; not offered" )
        return evidence


class ApprovedRepairApplier:
    """IRepairApplier: a verified proposal, approved by a person, becomes the
    step's first locator. The old locators stay behind it as fallbacks."""

    def __init__( self, store: ILocatorRepairStore, repository: IRecordingRepository ) -> None:
        self._store      = store
        self._repository = repository

    def apply( self, repair_id: int, approved: bool ) -> tuple[ bool, str ]:
        proposal = self._store.get( repair_id )
        if proposal is None:
            return False, f"no repair #{ repair_id }"
        if proposal.applied:
            return True, f"repair #{ repair_id } was already applied"
        if not proposal.verified:
            return False, f"repair #{ repair_id } was never verified on a live page; it cannot be applied"
        if not approved:
            return False, f"repair #{ repair_id } needs approval (--yes) before it changes the playback"
        found = self._repository.step( proposal.step_id )
        if found is None:
            return False, f"step #{ proposal.step_id } no longer exists"
        _, step = found
        first   = proposal.candidate.model_copy( update={ "rank": min([ c.rank for c in step.locators ] + [ 10 ]) - 1 })
        others  = [ c for c in step.locators if ( c.strategy, c.value ) != ( first.strategy, first.value )]
        self._repository.update_step( step.model_copy( update={ "locators": [ first, *others ]}))
        self._store.mark_applied( repair_id )
        return True, f"repair #{ repair_id } applied to step { step.position }: { first.value }"


class DiagnosingRecovery:
    """IPlaybackRecovery for an ad hoc step that is not saved anywhere:
    evidence only, since there is no step a repair could be attached to."""

    def __init__( self, diagnoser: IFailureDiagnoser ) -> None:
        self._diagnoser = diagnoser

    def recover( self, browser: IBrowserAdapter, step: RecordingStep, resolution: Resolution | None,
                 error: str, run_id: int ) -> StepDiagnostics:
        return self._diagnoser.diagnose( browser, step, resolution, error, run_id )
