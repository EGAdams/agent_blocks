"""Read a destination address directly off a receipt image, one phase per question.

Built for the case `calculate_distance.py`'s own resolver never reaches: a
chain-vendor description ("Mister - Mister Leonard car wash", expense 468)
that passes the mechanical "is this already usable" check in
`validate_destination` -- so no agent is ever asked -- and gets geocoded
from the vendor's own words instead of the real address, which was sitting
in plain sight on the receipt the whole time. This module is that missing
read, usable on its own or as the `--receipt` path handed to
`adw_calculate_distance.py`'s resolver.

Two phases:

* `ocr_text` (code) -- tesseract reads the receipt image's raw text. Free,
  local, and right most of the time for a printed receipt with the address
  near the top; no agent call is spent on a receipt OCR can already read.
* `extract_address` (code) -- look for exactly one confident street-address
  line in that text: a leading house number, and either a street-suffix
  word (St, Ave, Rd, ...) or a quadrant tag (NE/NW/SE/SW) somewhere in the
  rest of the line. Scanned receipts are noisy -- faded thermal print, a
  crooked photo, a street name tesseract misreads letter-for-letter -- so
  this stays narrow on purpose: zero matches or more than one is reported
  as not confident rather than guessed at, the same "too ambiguous to pick
  one silently" rule `DocumentDistanceCalculator` uses for a vendor name
  with two recorded locations.

Escalating to an agent (haiku, "locator" by default, same contract
`calculate_distance.py`'s own resolver already uses -- `DestinationOutput`
and `gates.destination_locatable`) only happens when OCR comes back empty or
not confident. The vision read is the expensive path, reserved for exactly
the receipts mechanical OCR cannot handle: skewed photos, handwriting,
thermal fade, two candidate lines a human would need to pick between.
"""

from __future__ import annotations

import re
from pathlib import Path

from .data_types import AddressExtraction, ReceiptAddressRequest

_STREET_SUFFIX = r"(?:St|Ave|Rd|Dr|Blvd|Ln|Hwy|Pkwy|Ct|Way|Pl|Cir|Trl|Ter|Hgts|Sq)\.?"
_NUMBER_LEAD = re.compile(r"^\s*(\d{1,6})\s+\S.*$")
_SUFFIX_WORD = re.compile(rf"\b{_STREET_SUFFIX}\b", re.IGNORECASE)
_QUADRANT_WORD = re.compile(r"\b(NE|NW|SE|SW)\b", re.IGNORECASE)


def _candidate_lines(text: str) -> list[str]:
    """Lines that read like a street address: a leading house number, plus a
    street-suffix word or a quadrant tag somewhere after it. Either signal
    alone is common in receipt noise (phone numbers, item counts, "NW" as
    part of a store brand); requiring the pair is what keeps this from
    matching an item line or a running total."""
    candidates = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or not _NUMBER_LEAD.match(line):
            continue
        if _SUFFIX_WORD.search(line) or _QUADRANT_WORD.search(line):
            candidates.append(line)
    return candidates


def ocr_text(run, request: ReceiptAddressRequest) -> str:
    """Tesseract's raw read of the receipt image. Never raises: a missing
    file, a missing `pytesseract`/Pillow install, or a corrupt image are all
    routine reasons OCR has nothing to offer, not reasons to crash the run --
    `extract_address` below treats empty text exactly like "not confident"
    and the agent fallback takes it from there.
    """
    path = Path(request.receipt_path) if request.receipt_path else None
    if not path or not path.is_file():
        run.console.note(f"receipt address: {path or '(no path given)'} does not exist")
        return ""
    try:
        import pytesseract
        from PIL import Image
    except ImportError as error:
        run.console.note(
            f"receipt address: OCR unavailable ({error}); going straight to the resolver")
        return ""
    try:
        with Image.open(path) as image:
            text = pytesseract.image_to_string(image)
    except Exception as error:  # noqa: BLE001 - a bad scan is routine, not fatal
        run.console.note(f"receipt address: OCR failed on {path} ({error})")
        return ""
    run.console.note(f"receipt address: OCR read {len(text)} character(s) from {path}")
    return text


def extract_address(text: str) -> AddressExtraction:
    """Phase 2's verdict: is there already one confident address-shaped line
    in the OCR text, or is the read too fuzzy to trust without an agent."""
    lines = _candidate_lines(text or "")
    if len(lines) == 1:
        return AddressExtraction(
            candidate=lines[0], confident=True,
            reason="one line reads a house number with a street suffix or quadrant tag",
        )
    if not lines:
        return AddressExtraction(
            reason="no line in the OCR text reads like a street address"
        )
    return AddressExtraction(
        reason=f"{len(lines)} address-shaped lines, too ambiguous to pick one: "
               f"{'; '.join(lines)}"
    )


def resolver_prompt(prompt: str, request: ReceiptAddressRequest,
                     extraction: AddressExtraction) -> str:
    """What the resolver is told: the ask, and what the mechanical OCR pass
    already tried -- same reasoning as `calculate_distance.resolver_prompt`:
    a resolver blind to what OCR already attempted tends to rebuild the same
    unusable read instead of looking past it."""
    lines = [
        prompt,
        "",
        f"A mechanical OCR pass over the receipt: {extraction.reason}",
    ]
    if extraction.candidate:
        lines.append(f"The closest OCR guess: {extraction.candidate}")
    lines.append(f"Receipt: {request.receipt_path or '(none)'}")
    lines.append("Read the destination address out of what is there.")
    return "\n".join(lines)


def finalize(extraction: AddressExtraction, resolved, resolver: str):
    """Phase 3's verdict: the winning address, if any, and where it came
    from -- OCR's own confident read beats asking an agent to re-read what
    was already legible, and an agent's answer is trusted only when it
    actually names a place (`locatable` and a non-blank `address`)."""
    from .data_types import ReceiptAddressResult

    if extraction.confident:
        return ReceiptAddressResult(
            address=extraction.candidate, source="ocr",
            message=f"{extraction.candidate} (read mechanically from the receipt)",
        )
    if resolved is not None and resolved.locatable and resolved.address.strip():
        label = resolver or "the resolver"
        return ReceiptAddressResult(
            address=resolved.address, source="agent",
            message=f"{resolved.address} (read by {label} from the receipt image)",
        )
    reason = (resolved.summary.strip() if resolved is not None else "") or extraction.reason
    return ReceiptAddressResult(message=f"No address could be read off the receipt: {reason}")


__all__ = ["extract_address", "finalize", "ocr_text", "resolver_prompt"]
