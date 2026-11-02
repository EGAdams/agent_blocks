"""Scala's records. Pydantic, because they cross the ADW boundary and land in the ledger."""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class Method( str, Enum ):
    """Who decided what a scan is."""
    MAZDA    = "mazda"      # Mazda's own intake classifier already claimed it
    RULES    = "rules"      # Scala's OCR pattern rules, >= the threshold
    HAIKU    = "haiku"      # Scala asked Haiku because the rules were unsure
    OPERATOR = "operator"   # a person named the folder (--folder)
    NONE     = "none"       # nobody could tell


class Decision( str, Enum ):
    FILED     = "filed"       # copied into the tree
    INBOX     = "inbox"       # nobody was sure; waiting in scanned_documents/inbox for a person
    MAZDA     = "mazda"       # finance paperwork: Mazda's, left alone
    DUPLICATE = "duplicate"   # this exact image was handled before
    ERROR     = "error"       # the scan could not be read or copied


#: The folder name that means "finance paperwork -- Mazda's, not Scala's".
FINANCE = "finance"
#: The answer that means "I can't tell".
UNSURE = "unsure"


class CategoryRule( BaseModel ):
    """One kind of document Scala can recognise by its words. Data, not code."""

    model_config = ConfigDict( extra="forbid", frozen=True )

    folder: str = Field( min_length=1 )    # "bills_511/electric", or FINANCE
    strong: tuple[ str, ... ] = ()         # words that nearly prove it
    weak:   tuple[ str, ... ] = ()         # words that only point at it


class Classification( BaseModel ):
    model_config = ConfigDict( extra="forbid", frozen=True )

    folder:     str                         # a cabinet folder, FINANCE, or UNSURE
    confidence: float = Field( ge=0.0, le=1.0 )
    method:     Method
    reason:     str = ""


class FilingOutcome( BaseModel ):
    """What happened to one scan. One ledger line each."""

    model_config = ConfigDict( extra="forbid" )

    at:             datetime
    source:         str
    sha256:         str
    decision:       Decision
    folder:         str = ""
    destination:    str = ""
    method:         Method = Method.NONE
    confidence:     float | None = None
    reason:         str = ""
    created_folder: bool = False
