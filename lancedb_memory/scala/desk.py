"""ScalaDesk: one scan in, one FilingOutcome out. The order is the policy.

1. Seen this exact image before?      -> DUPLICATE, nothing copied.
2. Mazda's classifier claimed it?     -> MAZDA, left alone (receipts, statements).
3. OCR + rules >= 0.90?               -> FILED by rules, no model.
4. Otherwise ask Haiku.  FINANCE      -> MAZDA, unless the dashboard already
                                         skipped her turn on it -> INBOX.
                         >= 0.70 and an allowed folder -> FILED (folder made if new).
                         anything else -> INBOX, for a person.

Every path ends in exactly one ledger line. The ADW runs these steps as its
phases; `handle()` runs them in one go for the watcher's tests.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from .interfaces import IDocumentClassifier, IFilingCabinet, IMazdaVerdicts, IOcrReader, IScalaLedger
from .mazda_verdicts import MAZDA_KINDS
from .models import FINANCE, UNSURE, Classification, Decision, FilingOutcome, Method

RULES_THRESHOLD = 0.90
HAIKU_THRESHOLD = 0.70


def sha256_of( image: Path ) -> str:
    digest = hashlib.sha256()
    with image.open( "rb" ) as handle:
        for chunk in iter( lambda: handle.read( 1 << 20 ), b"" ):
            digest.update( chunk )
    return digest.hexdigest()


class ScalaDesk:
    def __init__( self,
                  ocr:     IOcrReader,
                  mazda:   IMazdaVerdicts,
                  rules:   IDocumentClassifier,
                  haiku:   IDocumentClassifier,
                  cabinet: IFilingCabinet,
                  ledger:  IScalaLedger ) -> None:
        self._ocr = ocr
        self._mazda = mazda
        self._rules = rules
        self._haiku = haiku
        self._cabinet = cabinet
        self._ledger = ledger

    # -- the steps, in order --------------------------------------------------

    def already_handled( self, image: Path, sha256: str ) -> FilingOutcome | None:
        previous = self._ledger.seen( sha256 )
        if previous is None:
            return None
        return self.outcome( image, sha256, Decision.DUPLICATE, folder=previous.folder,
                              destination=previous.destination, reason=f"was { previous.decision.value }" )

    def mazda_claim( self, image: Path ) -> Classification | None:
        kind = self._mazda.doc_kind( image )
        if kind and kind.lower() in MAZDA_KINDS:
            return Classification( folder=FINANCE, confidence=1.0, method=Method.MAZDA,
                                   reason=f"Mazda's intake called it a { kind }" )
        return None

    def read_text( self, image: Path ) -> str:
        return self._ocr.read( image )

    def classify( self, text: str, image: Path ) -> Classification:
        folders = self._cabinet.folders()
        by_rules = self._rules.classify( text, image, folders )
        if by_rules.folder != UNSURE and by_rules.confidence >= RULES_THRESHOLD:
            return by_rules
        return self._haiku.classify( text, image, folders )

    def file( self, image: Path, sha256: str, verdict: Classification ) -> FilingOutcome:
        if verdict.folder == FINANCE and not self._mazda.left_for_scala( image ):
            return self.outcome( image, sha256, Decision.MAZDA, verdict=verdict )
        if verdict.folder == FINANCE:
            # Mazda's turn was skipped as not financial, Scala reads it as finance:
            # the two disagree, so a person decides rather than nobody.
            verdict = verdict.model_copy( update={ "folder": UNSURE, "reason":
                "looks financial, but Mazda's turn was skipped as not financial; "
                f"needs a person: { verdict.reason }" })
        sure = self.is_sure( verdict )
        try:
            if sure and self._cabinet.is_allowed( verdict.folder ):
                destination, created = self._cabinet.file( image, verdict.folder, sha256 )
                return self.outcome( image, sha256, Decision.FILED, verdict=verdict,
                                      destination=str( destination ), created_folder=created )
            destination = self._cabinet.inbox( image, sha256 )
        except OSError as exc:
            return self.outcome( image, sha256, Decision.ERROR, verdict=verdict,
                                  reason=f"copy failed: { exc }" )
        why = verdict.reason
        if verdict.folder not in ( UNSURE, "" ) and not self._cabinet.is_allowed( verdict.folder ):
            why = f"suggested { verdict.folder!r}, which Scala may not create; { why }"
        return self.outcome( image, sha256, Decision.INBOX, verdict=verdict,
                              destination=str( destination ), reason=why )

    @staticmethod
    def is_sure( verdict: Classification ) -> bool:
        """Sure enough to file without asking: a rule, a person, or Haiku at >= 0.70."""
        return verdict.method in ( Method.RULES, Method.OPERATOR ) or verdict.confidence >= HAIKU_THRESHOLD

    # -- all of it ------------------------------------------------------------

    def handle( self, image: Path ) -> FilingOutcome:
        outcome = self._decide( image )
        self._ledger.record( outcome )
        return outcome

    def _decide( self, image: Path ) -> FilingOutcome:
        if not image.is_file() or image.stat().st_size == 0:
            return self.outcome( image, "", Decision.ERROR, reason="the scan is missing or empty" )
        sha256 = sha256_of( image )
        duplicate = self.already_handled( image, sha256 )
        if duplicate is not None:
            return duplicate
        claim = self.mazda_claim( image )
        if claim is not None:
            return self.file( image, sha256, claim )
        return self.file( image, sha256, self.classify( self.read_text( image ), image ))

    def record( self, outcome: FilingOutcome ) -> None:
        self._ledger.record( outcome )

    def outcome( self, image: Path, sha256: str, decision: Decision,
                  verdict: Classification | None = None, reason: str | None = None,
                  **fields ) -> FilingOutcome:
        return FilingOutcome(
            at=datetime.now().astimezone(), source=str( image ), sha256=sha256, decision=decision,
            folder=fields.pop( "folder", verdict.folder if verdict else "" ),
            method=verdict.method if verdict else Method.NONE,
            confidence=verdict.confidence if verdict else None,
            reason=reason if reason is not None else ( verdict.reason if verdict else "" ),
            **fields,
        )
