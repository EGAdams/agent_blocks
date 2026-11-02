"""Scala's cheap classifier: OCR words against a table of document kinds. No model.

Every bill is a household bill, so the rules only decide
*which* bill or document it is, never whose. Receipts and statements are on
the table too, as FINANCE, so a page Mazda's classifier missed still goes
back to being Mazda's rather than being filed here.

Confidence is deliberately coarse and easy to read back:

* two different strong words for one kind    -> 0.97
* one strong word plus a weak one             -> 0.92
* one strong word alone                       -> 0.85
* weak words only                             -> 0.50 + 0.10 each, at most 0.75
* a *second* kind with a strong word too      -> at most 0.75 (two kinds fit: ask Haiku)

So ">= 0.90" means "at least one word that nearly proves it, backed by a
second, and nothing else on the page claims it". A combined Consumers Energy
electric-and-gas bill hits two kinds and goes to Haiku, as it should.
"""
from __future__ import annotations

import re
from pathlib import Path

from .models import FINANCE, UNSURE, CategoryRule, Classification, Method


RULES: tuple[ CategoryRule, ... ] = (
    CategoryRule( folder="bills_511/electric",
                  strong=( "kwh", "kilowatt hour", "kilowatt hours", "electric service", "electric charges" ),
                  weak=( "electric", "electricity", "consumers energy", "meter reading" )),
    CategoryRule( folder="bills_511/gas",
                  strong=( "therms", "ccf", "mcf", "natural gas", "gas charges", "gas service" ),
                  weak=( "gas", "dte", "semco", "consumers energy", "meter reading" )),
    CategoryRule( folder="bills_511/water",
                  strong=( "water service", "water usage", "sewer", "stormwater", "water department",
                           "water charges" ),
                  weak=( "water", "gallons", "cubic feet" )),
    CategoryRule( folder="bills_511/mortgage",
                  strong=( "mortgage statement", "escrow", "principal balance", "loan number",
                           "mortgage payment" ),
                  weak=( "mortgage", "principal", "interest", "servicer" )),
    CategoryRule( folder="bills_511/xfinity",
                  strong=( "xfinity", "comcast" ),
                  weak=( "internet", "cable", "streaming" )),
    CategoryRule( folder="documents_511/probate",
                  strong=( "probate court", "estate of", "personal representative", "decedent",
                           "letters of authority", "25-217344" ),
                  weak=( "probate", "estate", "court", "heirs" )),
    CategoryRule( folder=FINANCE,
                  strong=( "subtotal", "change due", "thank you for shopping", "beginning balance",
                           "ending balance", "statement period", "new balance", "minimum payment due" ),
                  weak=( "receipt", "total", "sales tax", "visa", "mastercard", "cashier" )),
)


def _normalise( text: str ) -> str:
    return " ".join( text.lower().split())


def _hits( text: str, words: tuple[ str, ... ]) -> set[ str ]:
    return { w for w in words if re.search( rf"(?<![a-z0-9]){ re.escape( w ) }(?![a-z0-9])", text )}


def _confidence( strong: int, weak: int ) -> float:
    if strong >= 2:
        return 0.97
    if strong == 1:
        return 0.92 if weak >= 1 else 0.85
    return min( 0.50 + 0.10 * weak, 0.75 ) if weak else 0.0


class RuleClassifier:
    """IDocumentClassifier over a table of CategoryRules."""

    def __init__( self, rules: tuple[ CategoryRule, ... ] = RULES ) -> None:
        self._rules = rules

    def classify( self, text: str, image: Path, folders: tuple[ str, ... ] = ()) -> Classification:
        page = _normalise( text )
        if len( page ) < 20:
            return Classification( folder=UNSURE, confidence=0.0, method=Method.RULES,
                                   reason="OCR found almost no text on the page" )
        scored = []
        for rule in self._rules:
            strong, weak = _hits( page, rule.strong ), _hits( page, rule.weak )
            if strong or weak:
                scored.append(( _confidence( len( strong ), len( weak )), rule.folder, strong, weak ))
        if not scored:
            return Classification( folder=UNSURE, confidence=0.0, method=Method.RULES,
                                   reason="no known document words on the page" )
        scored.sort( key=lambda s: s[ 0 ], reverse=True )
        confidence, folder, strong, weak = scored[ 0 ]
        rivals = [ s[ 1 ] for s in scored[ 1: ] if s[ 2 ]]
        if rivals:
            confidence = min( confidence, 0.75 )
        words = ", ".join( sorted( strong | weak ))
        reason = f"words: { words }" + ( f"; also looks like { ', '.join( rivals ) }" if rivals else "" )
        return Classification( folder=folder, confidence=confidence, method=Method.RULES, reason=reason )
