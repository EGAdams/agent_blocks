"""Validation gates: verify the envelope's CLAIMS, never guesses.

A gate is `gate(envelope, run) -> GateReport` — one check per item it looked at.
Violations are derived from the failed checks and sent back to the SAME agent
session as a correction. Every check is recorded either way, so a green gate
says WHAT it verified instead of only that it passed.

Gates check what is mechanically checkable; plan quality is a reviewer's job.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .data_types import EnvelopeBase, GateReport

TAIL_CHARS = 1000        # command output kept as evidence on a failure


def _size(path: Path) -> str:
    n = path.stat().st_size
    return f"{n}B" if n < 1024 else f"{n / 1024:.1f}KB"


def artifacts_exist(envelope: EnvelopeBase, run) -> GateReport:
    report = GateReport()
    for a in envelope.artifacts:
        p = Path(a)
        report.check(a, p.exists(),
                     f"exists, {_size(p)}" if p.exists() else "declared artifact does not exist")
    return report


def files_non_empty(envelope: EnvelopeBase, run) -> GateReport:
    report = GateReport()
    for a in envelope.artifacts:
        p = Path(a)
        if not (p.exists() and p.is_file()):
            continue                       # existence is artifacts_exist's job
        empty = p.stat().st_size == 0
        report.check(a, not empty, "declared artifact is empty" if empty else _size(p))
    return report


def json_parses(envelope: EnvelopeBase, run) -> GateReport:
    report = GateReport()
    for a in envelope.artifacts:
        p = Path(a)
        if p.suffix != ".json" or not p.exists():
            continue
        try:
            parsed = json.loads(p.read_text())
            report.check(a, True, f"parses, {type(parsed).__name__}")
        except json.JSONDecodeError as e:
            report.check(a, False, f"declared JSON artifact does not parse: {e}")
    return report


def diff_matches_claims(envelope: EnvelopeBase, run) -> GateReport:
    """Every file claimed changed must exist on disk."""
    report = GateReport()
    for f in getattr(envelope, "changed_files", []):
        p = Path(f)
        report.check(f, p.exists(),
                     f"exists, {_size(p)}" if p.exists() else "claimed changed file does not exist")
    return report


def verdict_consistent(envelope: EnvelopeBase, run) -> GateReport:
    """A review's verdict must agree with the findings it just wrote down.

    Nothing here judges the code — that is the reviewer's job. This checks the
    envelope against itself: an approval that ships blocking items, or a
    rejection that names no problem, is a claim the harness can refute without
    reading a line of the diff.
    """
    report = GateReport()
    approved = bool(getattr(envelope, "approved", False))
    blocking = list(getattr(envelope, "blocking", []))
    unmet = [f.requirement for f in getattr(envelope, "findings", []) if not f.met]

    report.check("approved vs blocking", not (approved and blocking),
                 "no blocking items" if not blocking
                 else f"{len(blocking)} blocking item(s) while approved=true"
                 if approved else f"{len(blocking)} blocking item(s), not approved")
    report.check("approved vs findings", not (approved and unmet),
                 "every requirement met" if not unmet
                 else f"{len(unmet)} unmet requirement(s) while approved=true"
                 if approved else f"{len(unmet)} unmet requirement(s), not approved")
    report.check("rejection names a problem", approved or bool(blocking or unmet),
                 "verdict is supported" if approved or blocking or unmet
                 else "approved=false but no blocking item or unmet requirement was given")
    return report


def map_link_openable(envelope: EnvelopeBase, run) -> GateReport:
    """A resolved map link must survive the same check the stored one failed.

    The agent is asked for a link, so the link is the claim — verified with the
    exact predicate the launch phase will apply, not a looser one. An agent that
    returns an address but no usable link has not finished the job.

    The exception is a declared miss: `locatable: false`, which says the source
    names no place at all. Some receipts genuinely do not, and a gate that
    rejected that answer would be demanding a link the source cannot support —
    which is a gate that manufactures fabricated addresses. A miss is held to
    saying what was seen instead, and the ADW decides what it means for the run.

    It is a dedicated field and not an empty `map_link` on purpose: a miss has to
    be something the agent states, so that forgetting the link still fails.
    """
    from .map_open import ALLOWED_SCHEMES, MapOpenRequest, validate

    report = GateReport()
    link = str(getattr(envelope, "map_link", "") or "")
    if not getattr(envelope, "locatable", True):
        report.check("declared miss", bool(envelope.summary.strip()),
                     envelope.summary.strip()
                     or "reported nothing locatable without saying what was seen")
        return report.check("no link with the miss", not link.strip(),
                            "none offered" if not link.strip()
                            else "reported nothing locatable, then offered a link anyway")
    report.check("map_link", bool(link.strip()),
                 "present" if link.strip() else "agent returned no map link")
    if link.strip():
        check = validate(run, MapOpenRequest(url=link), source="agent")
        report.check("map_link openable", check.openable,
                     check.reason if check.openable
                     else f"{check.reason} — must be {'/'.join(ALLOWED_SCHEMES)} with a host")
    address = str(getattr(envelope, "address", "") or "")
    report.check("address", bool(address.strip()),
                 "read off the receipt" if address.strip()
                 else "no address given, so the link cannot be checked by a human")
    return report


def destination_locatable(envelope: EnvelopeBase, run) -> GateReport:
    """A resolved destination must name a place, or declare that it cannot.

    Same shape as map_link_openable's "declared miss" rule: `locatable: false`
    is accepted outright as long as the agent says what it saw, because some
    receipts genuinely name no place. Otherwise an `address` is required —
    there is no URL to validate here, since the destination is free text
    Maps itself will geocode.
    """
    report = GateReport()
    address = str(getattr(envelope, "address", "") or "")
    if not getattr(envelope, "locatable", True):
        return report.check("declared miss", bool(envelope.summary.strip()),
                            envelope.summary.strip()
                            or "reported nothing locatable without saying what was seen")
    report.check("address", bool(address.strip()),
                 "read off the receipt" if address.strip()
                 else "no address given, and no miss was declared either")
    return report


def tests_pass(command: str):
    """Gate factory: the given shell command must exit 0."""
    def gate(envelope: EnvelopeBase, run) -> GateReport:
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        ok = result.returncode == 0
        note = f"exit {result.returncode}"
        if not ok:
            note += "\n" + (result.stdout + result.stderr)[-TAIL_CHARS:]
        return GateReport().check(command, ok, note)
    gate.__name__ = f"tests_pass({command})"
    return gate
