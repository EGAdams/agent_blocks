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

import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

from .calculate_distance import (SERP_API_ATTEMPTS, SERP_API_KEY_ENV, SERP_API_LOCATION,
                                 SERP_API_TIMEOUT, SERP_API_URL, _key_from_bashrc)
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
    text, failures = _tesseract_text(path), []
    if not text:
        failures.append("tesseract unavailable or empty")
        text = _rapidocr_text(path)
    if not text:
        run.console.note(f"receipt address: OCR read nothing from {path} ({'; '.join(failures)})")
        return ""
    run.console.note(f"receipt address: OCR read {len(text)} character(s) from {path}")
    return text


def _tesseract_text(path: Path) -> str:
    """Tesseract's read, or '' -- it needs a system binary that a box without
    sudo cannot always get, which is why `_rapidocr_text` exists."""
    try:
        import pytesseract
        from PIL import Image
        with Image.open(path) as image:
            return pytesseract.image_to_string(image)
    except Exception:  # noqa: BLE001 - a bad scan or a missing binary is routine
        return ""


def _rapidocr_text(path: Path) -> str:
    """The pure-pip fallback: one line of text per detected box, or ''."""
    try:
        from rapidocr_onnxruntime import RapidOCR
        found, _ = RapidOCR()(str(path))
        return "\n".join(box[1] for box in (found or []))
    except Exception:  # noqa: BLE001
        return ""


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


#: Chain receipts print no street address, only which store rang the sale.
#: Home Depot's header line is `<store> <register> <transaction> <date> <time>`
#: ("2720 00002 66148 08/28/25 12:40 PM"); other chains say "STORE #123".
_HEADER_LINE = re.compile(r"^\s*(\d{3,5})\s+\d{4,5}\s+\d{4,6}\s+\d{1,2}/\d{1,2}/\d{2,4}\b", re.M)
#: A pure-pip OCR drops the spaces: "2720 00002 66148" comes back as one
#: 14-digit run, store (4) + register (5) + transaction (5), then the date.
_FUSED_HEADER_LINE = re.compile(r"^\s*(\d{4})\d{5}\d{5}\s*$", re.M)
_STORE_LABEL = re.compile(r"\bstore\s*(?:#|no\.?|number)?\s*:?\s*(\d{2,5})\b", re.IGNORECASE)


def extract_store_number(text: str) -> str:
    """The store number a chain receipt carries in place of an address, or ''.
    Both patterns are narrow on purpose -- a wrong number would verify
    nothing and only waste a search."""
    match = (_HEADER_LINE.search(text or "") or _FUSED_HEADER_LINE.search(text or "")
             or _STORE_LABEL.search(text or ""))
    return match.group(1) if match else ""


def store_brand(vendor: str) -> str:
    """The chain's name out of an expense description. Bank text pads its
    columns ("THE HOME DEPOT      WALKER              MI"), so the brand is
    whatever precedes the first run of two or more spaces."""
    return re.split(r"\s{2,}", (vendor or "").strip())[0].strip()


def _carries_store_number(value: object, store_number: str) -> bool:
    return bool(re.search(rf"(?<!\d){re.escape(store_number)}(?!\d)", json.dumps(value)))


def address_for_store(data: dict, store_number: str) -> tuple[str, str]:
    """(address, title) from a SerpAPI response, only when the result itself
    names this store number. Google geocodes the words, not the number, so
    an unverified card is just the chain's nearest branch to Grand Rapids --
    right by luck when the receipt came from there, wrong everywhere else.
    The knowledge card's own `website` is the check that works in practice:
    Home Depot's store pages end in the store number."""
    knowledge_graph = data.get("knowledge_graph") or {}
    if knowledge_graph.get("address") and _carries_store_number(knowledge_graph, store_number):
        return knowledge_graph["address"], knowledge_graph.get("title", "")
    return "", ""


def lookup_store_address(run, brand: str, store_number: str) -> tuple[str, str]:
    """(address, message). Never raises, same rule as every phase here."""
    if not brand or not store_number:
        return "", "no chain name or store number to look up"
    api_key = os.environ.get(SERP_API_KEY_ENV, "").strip() or _key_from_bashrc()
    if not api_key:
        return "", f"{SERP_API_KEY_ENV} is not set -- cannot look up a store number"
    params = {"engine": "google", "q": f"{brand} #{store_number}",
              "location": SERP_API_LOCATION, "api_key": api_key}
    url = f"{SERP_API_URL}?{urllib.parse.urlencode(params)}"
    data, last_error = None, None
    for _ in range(SERP_API_ATTEMPTS):
        try:
            with urllib.request.urlopen(url, timeout=SERP_API_TIMEOUT) as response:  # noqa: S310
                data = json.load(response)
            break
        except Exception as error:  # noqa: BLE001 - a failed search must not crash the run
            last_error = error
    if data is None:
        return "", f"SerpAPI search failed: {last_error}"
    address, title = address_for_store(data, store_number)
    if not address:
        return "", f"no search result confirmed {brand} #{store_number}"
    return address, f"{title or brand} #{store_number}"


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
    lines.append(
        "Chain receipts (Home Depot, Lowe's, ...) often print no street address, only a store "
        "number. If that is the case, answer `\"locatable\": false` with an empty `address` and put "
        "the digits of the store number in a `store_number` field of your JSON (e.g. "
        "`\"store_number\": \"2720\"`). Never guess one: the first number on a Home Depot "
        "receipt's header line is the store, but a register or transaction number is not.")
    return "\n".join(lines)


def finalize(extraction: AddressExtraction, resolved, resolver: str,
             store_address: str = "", store_label: str = ""):
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
    if store_address:
        return ReceiptAddressResult(
            address=store_address, source="store",
            message=f"{store_address} (store number on the receipt: {store_label})",
        )
    if resolved is not None and resolved.locatable and resolved.address.strip():
        label = resolver or "the resolver"
        return ReceiptAddressResult(
            address=resolved.address, source="agent",
            message=f"{resolved.address} (read by {label} from the receipt image)",
        )
    reason = (resolved.summary.strip() if resolved is not None else "") or extraction.reason
    return ReceiptAddressResult(message=f"No address could be read off the receipt: {reason}")


__all__ = ["address_for_store", "extract_address", "extract_store_number", "finalize",
           "lookup_store_address", "ocr_text", "resolver_prompt", "store_brand"]
