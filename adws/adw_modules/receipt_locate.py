"""Find the real file behind a stale `expenses.receipt_url`, one phase per question.

`expense_review/receipt_opener.py` (rol_finances) refuses outright the moment
its one-shot rglob comes up ambiguous or empty: a receipt filed as
`walgreens_01_22_25_9_85.jpg` that the row remembers as
`walgreens_01_23_25_9_85.jpg` (the scanner misread the day by one) looks, from
inside that single exact lookup, exactly like a receipt that was never
scanned at all. It almost always IS still on disk, just a few tokens off from
what the row remembers, so this module tries the same exact lookup first and
only then ranks every file in the receipts tree by how much of the stored
filename it shares.

Picking among the ranked candidates is left to a human (`select_match` in
`adws/adw_receipt_locate.py`), not an agent: which scrawled receipt is the
real one is exactly the kind of one-glance judgement a human makes faster and
more reliably than a model would, and getting it wrong writes a wrong
filename back onto a live row with nothing left to flag the mistake.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from .data_types import (ReceiptCandidate, ReceiptLocateRequest, ReceiptLocateResult,
                         ReceiptSearch, ReceiptSelection)

#: The rol_finances checkout's receipt tree. Duplicated rather than imported --
#: same reason as `adw_modules/calculate_distance.py`'s VENDOR_YAML_PATH: that
#: tree lives in a different repo than this module does.
RECEIPTS_BASE_DIR = Path.home() / "rol_finances" / "readable_documents" / "receipts"

_TOKEN = re.compile(r"[a-z0-9]+")
MAX_CANDIDATES = 8
#: Below this a "match" is noise, not a choice worth putting in front of a human.
MIN_SCORE = 0.25


def _tokens(name: str) -> set[str]:
    return set(_TOKEN.findall(name.lower()))


def is_openable(url: str) -> bool:
    """Same scheme-check `map_opener.is_openable` uses: a real http(s) link,
    not a bare filename. The handful of receipts that ARE links (Amazon order
    confirmations) have nothing to search for on disk."""
    parsed = urlparse((url or "").strip())
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _base_dir(request: ReceiptLocateRequest) -> Path:
    return Path(request.receipts_dir) if request.receipts_dir else RECEIPTS_BASE_DIR


def resolve_direct(request: ReceiptLocateRequest) -> str:
    """The real file the stored value already points at, or "" if it does not.

    Mirrors `receipt_opener._resolve`'s unambiguous path only: a direct path
    under the base dir, or a single rglob match by exact filename. An
    ambiguous one (more than one file sharing that exact name) is left to the
    ranked search below rather than guessed at here.
    """
    stored = (request.receipt_url or "").strip()
    if not stored or is_openable(stored):
        return ""
    base = _base_dir(request)
    candidate = Path(stored)
    if candidate.is_absolute():
        return str(candidate) if candidate.exists() else ""
    direct = base / stored
    if direct.exists():
        return str(direct)
    if not base.is_dir():
        return ""
    matches = [m for m in base.rglob(candidate.name) if m.is_file()]
    return str(matches[0]) if len(matches) == 1 else ""


def search_candidates(run, request: ReceiptLocateRequest) -> ReceiptSearch:
    """Rank every file under the receipts tree by token overlap with the stored name.

    The stale filename is itself the best clue to what the real one looks
    like: `walgreens_01_23_25_9_85.jpg` and the real
    `walgreens_01_22_25_9_85.jpg` share the vendor token and the amount
    token and differ only in the day -- exactly the kind of one-field slip
    jaccard overlap on filename tokens scores highest, and exactly the kind a
    human fixes in seconds once it is in front of them.
    """
    direct = resolve_direct(request)
    if direct:
        run.console.note(f"receipt: stored value already resolves — {direct}")
        return ReceiptSearch(resolved_direct=True, direct_path=direct)

    stored = (request.receipt_url or "").strip()
    base = _base_dir(request)
    if not stored:
        reason = "no receipt filename on this expense"
    elif is_openable(stored):
        reason = "stored value is a link, not a filename"
    elif not base.is_dir():
        reason = f"{base} does not exist"
    else:
        reason = ""
    if reason:
        run.console.note(f"receipt search: {reason}")
        return ReceiptSearch(reason=reason)

    wanted = _tokens(Path(stored).stem)
    scored: list[ReceiptCandidate] = []
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        have = _tokens(path.stem)
        if not have or not wanted:
            continue
        overlap = len(wanted & have) / len(wanted | have)
        if overlap >= MIN_SCORE:
            scored.append(ReceiptCandidate(path=str(path), relative=str(path.relative_to(base)),
                                           score=round(overlap, 3)))
    scored.sort(key=lambda c: c.score, reverse=True)
    top = scored[:MAX_CANDIDATES]
    reason = (f"{len(top)} candidate(s) under {base}" if top
             else f"no file under {base} shares enough of {stored!r} to guess")
    run.console.note(f"receipt search: {reason}")
    return ReceiptSearch(candidates=top, reason=reason)


def format_choices(search: ReceiptSearch) -> str:
    """The numbered list shown to the human in `select_match`."""
    lines = [f"{i}. {c.relative}  (match {c.score:.0%})"
             for i, c in enumerate(search.candidates, start=1)]
    lines.append("0. none of these")
    return "\n".join(lines)


def parse_choice(raw: str, search: ReceiptSearch) -> ReceiptSelection:
    """Turn the human's typed answer into a selection. Anything not a valid
    1-based index into `search.candidates` -- blank, "0", garbage -- is "none"."""
    raw = (raw or "").strip()
    if not raw or raw == "0":
        return ReceiptSelection(picked=False)
    try:
        index = int(raw)
    except ValueError:
        return ReceiptSelection(picked=False)
    if 1 <= index <= len(search.candidates):
        return ReceiptSelection(picked=True, candidate=search.candidates[index - 1])
    return ReceiptSelection(picked=False)


def _open_in_explorer(path: Path) -> str:
    """Same ladder `receipt_opener.ExplorerReceiptOpener` uses: wslpath, then
    `explorer.exe`, detached. Duplicated for the same cross-repo reason as
    `RECEIPTS_BASE_DIR` above."""
    try:
        windows_path = subprocess.check_output(["wslpath", "-w", str(path)], text=True).strip()
    except (OSError, subprocess.CalledProcessError) as error:
        return f"Could not convert the receipt path for Explorer: {error}"
    try:
        subprocess.Popen(["explorer.exe", windows_path], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError as error:
        return f"Could not start explorer.exe: {error}"
    return f"Opened the receipt in Explorer: {path}"


def finalize(run, request: ReceiptLocateRequest, search: ReceiptSearch,
            selection: ReceiptSelection) -> ReceiptLocateResult:
    """Open whatever was found or chosen, and report the corrected `receipt_url`.

    Three outcomes, in the order a caller cares about them: the direct lookup
    already worked (nothing to correct), a human confirmed a ranked
    candidate (open it, and report the correction), or nobody confirmed
    anything (say so plainly -- the same message shape the direct lookup
    itself would have produced).
    """
    if search.resolved_direct:
        message = _open_in_explorer(Path(search.direct_path))
        run.console.note(f"receipt finalize: {message}")
        return ReceiptLocateResult(message=message)
    if selection.picked and selection.candidate:
        message = _open_in_explorer(Path(selection.candidate.path))
        run.console.note(
            f"receipt finalize: {message} (receipt_url corrected to {selection.candidate.relative})")
        return ReceiptLocateResult(message=message, receipt_url=selection.candidate.relative)
    stored = (request.receipt_url or "").strip() or "(none)"
    message = f"Receipt file not found: {stored} (no match confirmed)"
    run.console.note(f"receipt finalize: {message}")
    return ReceiptLocateResult(message=message)


__all__ = ["RECEIPTS_BASE_DIR", "finalize", "format_choices", "is_openable", "parse_choice",
          "resolve_direct", "search_candidates"]
