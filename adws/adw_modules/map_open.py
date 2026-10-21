"""Open a map link in the Windows browser from WSL, one phase per question.

This is `expense_review/map_opener.py` (rol_finances) split along the seams it
already had. `WslMapOpener.open()` answers three separate questions in one
method -- is the link openable, which launcher exists on this box, did the spawn
happen -- and a failure anywhere returns the same kind of string, so the trace
of a failed map open told you only that it failed. Here each question is its own
callable with its own return type, which is what lets an ADW put an agent phase
between two of them (see MapLinkOutput) without touching the others.

The two invariants from the original are invariants here too:

* Never block. The launcher is spawned detached with its streams discarded.
  `explorer.exe` exits non-zero even on success, so its exit status is not a
  usable signal and is never waited on.
* Never raise. A missing or malformed link is routine data. `validate()` returns
  an unopenable LinkCheck and `launch()` returns a message -- the PHASE still
  succeeds, because looking and finding nothing is a job well done. Whether the
  RUN is accepted is the ADW's call via `run.finish(accepted=...)`.

One phase here has no counterpart in the original: `derive_from_place()`. The
original could only refuse when `map_link` was junk, but most such expenses do
say where they were — "wyoming walmart" — and Google Maps' own search takes that
string happily. So a missing link falls back to searching for the place before
any agent is considered, which keeps the common case free.

`wslview` is deliberately absent from the ladder: it is not installed on this box.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from urllib.parse import quote_plus, urlparse

from .data_types import (EventRecord, LauncherChoice, LauncherSpec, LaunchResult, LinkCheck,
                         MapOpenRequest, PlaceDerivation, VerifyOutput)
from .utils import now_iso, operator_env

#: Tried in order; the first launcher present on the box wins.
#:
#: `explorer.exe` is LAST among the Windows launchers, not first, and that
#: ordering is load-bearing. It silently refuses any URL carrying a query
#: string: handed
#: `https://www.google.com/maps/search/?api=1&query=MISTER+CAR+WASH`, it opens a
#: File Explorer window on the default folder and never reaches the browser --
#: `?` cannot appear in a Windows path, so the argument stops looking like a URL
#: to it. Measured on this box, 2/2 each way: no new browser process for the
#: query-string form, a new one every time for the same URL without a query, and
#: every other launcher below opens both. Since a real `expenses.map_link` from
#: the map-distance tool is full of `?api=1&...`, a ladder that tries
#: `explorer.exe` first is a ladder whose maps mostly do not open. It stays on
#: the ladder because it is the one launcher that needs no shell and it still
#: handles a bare `https://host/path`.
#:
#: `rundll32 url.dll,FileProtocolHandler` is the Windows shell's own URL opener
#: and is the honest first choice; `cmd.exe /c start` follows the convention
#: already in the rol_finances tree, and `xdg-open` is last as in
#: `start_receipt_scanner.sh:open_browser`.
LAUNCHERS: list[LauncherSpec] = [
    LauncherSpec(name="rundll32.exe",
                 argv=["rundll32.exe", "url.dll,FileProtocolHandler", "{url}"]),
    LauncherSpec(name="cmd.exe", argv=["cmd.exe", "/c", "start", "", "{url}"]),
    LauncherSpec(name="powershell.exe",
                 argv=["powershell.exe", "-NoProfile", "-Command", "Start-Process", "{url}"]),
    LauncherSpec(name="explorer.exe", argv=["explorer.exe", "{url}"]),
    LauncherSpec(name="xdg-open", argv=["xdg-open", "{url}"]),
]

ALLOWED_SCHEMES = ("http", "https")


def ladder(request: MapOpenRequest) -> list[LauncherSpec]:
    """The launchers this request runs against — its own, or the default ladder."""
    return list(request.launchers) if request.launchers else list(LAUNCHERS)


# ── Phase 1: is this a link we are willing to hand to a browser ───────────────

def validate(run, request: MapOpenRequest, source: str = "stored") -> LinkCheck:
    """Scheme-check the candidate link.

    Checked rather than merely non-empty: `expenses.map_link` is populated by the
    map-distance tool and has held junk before, and handing an arbitrary string
    to a launcher is how a stray argument becomes a shell surprise. The argv
    templates in LauncherSpec close that door too — this is the second lock on it.

    `source` records where the candidate came from, and is checked with exactly
    this predicate whoever produced it: a link a search built and a link an agent
    read get no more trust than the stored column got.
    """
    raw = request.url or ""
    stripped = raw.strip()
    if not stripped:
        check = LinkCheck(raw=raw, openable=False, reason="no map link on this expense")
    else:
        parsed = urlparse(stripped)
        if parsed.scheme not in ALLOWED_SCHEMES:
            check = LinkCheck(raw=raw, openable=False,
                              reason=f"scheme {parsed.scheme or '(none)'!r} is not http/https")
        elif not parsed.netloc:
            check = LinkCheck(raw=raw, openable=False, reason="no host in the link")
        else:
            check = LinkCheck(raw=raw, target=stripped, openable=True, reason="http(s) link with a host")
    check.source = source

    run.console.note(f"map link ({source}): "
                     f"{'openable' if check.openable else 'unusable'} — {check.reason}")
    return check


def unusable_message(check: LinkCheck) -> str:
    """The operator's one line when phase 1 says no. Same wording as before."""
    return f"No usable map link on this expense: {check.shown}"


# ── Phase 1b: no link, but the expense says where it was ──────────────────────
#
# "wyoming walmart" and "kentwood Ace Hardware" are not addresses and never will
# be, but they are exactly what Google Maps' own search takes. So the fallback is
# to let Maps do the geocoding instead of pretending to do it here: build a
# search URL and hand that over. No agent, no network call from this box, and the
# operator sees a map of the place they wrote down.

#: Google Maps' path-style search, and deliberately NOT the documented
#: `/maps/search/?api=1&query=` form. The two are the same search -- Google
#: redirects the second to the first -- but one of them has a query string, and a
#: URL with a query string is a URL `explorer.exe` throws away (see LAUNCHERS).
#: Picking the form with nothing after the host but a path means the link we
#: build is openable by every launcher on the ladder rather than all but one, so
#: a future ladder edit cannot quietly break map-opening again.
MAPS_SEARCH = "https://www.google.com/maps/search/{q}"

#: A parenthetical in a description is either bookkeeping aimed at a human --
#: "($10 wash + $2 tip per receipt note)" -- or part of the destination, as in
#: "Home Depot (Alpine Ave)". The two want opposite treatment: searching for the
#: first buries the vendor name in words no geocoder wants, while dropping the
#: second throws away the only thing that says WHICH Home Depot. So the aside is
#: read before it is judged: one that carries a money amount or bookkeeping
#: vocabulary goes away entirely, and any other is merely unwrapped.
PLACE_ASIDE = re.compile(r"\(([^)]*)\)?|\[([^\]]*)\]?|\{([^}]*)\}?")
PLACE_BOOKKEEPING = re.compile(r"(?i)[$€£]|\b(?:tip|note|notes|receipt|per|each|qty|"
                               r"total|tax|incl|excl|paid|cash|card|refund|est)\b")

#: Junk that shows up in a vendor line and means nothing to a geocoder.
PLACE_NOISE = re.compile(r"(?i)\b(?:ref|inv(?:oice)?|txn|trans|acct|card|x{2,}\d*)"
                         r"[#:\s-]*[\w-]*|[#*]|\b\d{6,}\b")
#: What a geocoder can use: letters, digits, and the punctuation in real names.
PLACE_KEEP = re.compile(r"[^0-9A-Za-z&'.,\- ]+")
MIN_PLACE_LETTERS = 4            # "ace" alone is not a destination; "walmart" is


def _aside(match: re.Match) -> str:
    """Drop a bookkeeping aside, unwrap any other. See PLACE_ASIDE."""
    inner = next(( group for group in match.groups() if group is not None ), "")
    return " " if PLACE_BOOKKEEPING.search(inner) else f" {inner} "


def place_query(text: str) -> str:
    """Normalize an expense's own words into something worth searching.

    Deliberately conservative — it drops reference numbers and symbols and
    collapses whitespace, and does not try to guess a city or expand an
    abbreviation. Guessing is the agent phase's job, and it only runs when what
    is left here is not legible.
    """
    without_asides = PLACE_ASIDE.sub(_aside, text or "")
    cleaned = PLACE_KEEP.sub(" ", PLACE_NOISE.sub(" ", without_asides))
    return " ".join(cleaned.split()).strip(" ,.-")


def derive_from_place(run, request: MapOpenRequest) -> PlaceDerivation:
    """Turn `request.place` into a maps-search link, or say why it cannot be.

    Legibility is the whole decision: enough letters to be a name rather than a
    receipt code. Below the bar nothing is fabricated — the derivation comes back
    unusable, which is the signal the ADW uses to spend an agent call.
    """
    query = place_query(request.place)
    letters = sum(ch.isalpha() for ch in query)
    if not query:
        derivation = PlaceDerivation(reason="the expense names no place to search for")
    elif letters < MIN_PLACE_LETTERS:
        derivation = PlaceDerivation(query=query,
                                     reason=f"{query!r} is not legible as a place name")
    else:
        derivation = PlaceDerivation(query=query, usable=True,
                                     link=MAPS_SEARCH.format(q=quote_plus(query)),
                                     reason=f"searching maps for {query!r}")
    run.console.note(f"map place: {derivation.reason}")
    return derivation


# ── Phase 2: which launcher is present on this box ────────────────────────────

def select_launcher(run, request: MapOpenRequest) -> LauncherChoice:
    """Walk the ladder and return the first launcher that exists. Spawns nothing.

    `shutil.which` is not used: it searches the caller's PATH, while the phase
    that actually spawns inherits the operator's environment (`operator_env`),
    and a probe against a different PATH than the launch is a probe of the wrong
    box. `which` is run as a subprocess under that same env instead.
    """
    env = operator_env()
    probed: list[str] = []
    for spec in ladder(request):
        probed.append(spec.name)
        if _present(spec.name, env):
            choice = LauncherChoice(name=spec.name, argv=spec.build("{url}"),
                                    probed=probed, found=True)
            run.console.note(f"map launcher: {spec.name} (probed {', '.join(probed)})")
            return choice
    run.console.note(f"map launcher: none found (probed {', '.join(probed)})")
    return LauncherChoice(probed=probed, found=False)


def _present(name: str, env: dict[str, str]) -> bool:
    try:
        found = subprocess.run(["which", name], capture_output=True, text=True, env=env, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return found.returncode == 0


def opened_message(check: LinkCheck, choice: LauncherChoice) -> str:
    """The operator's one line on success — and where the link came from.

    A stored link is a fact and needs no qualifier. A searched or agent-read
    location is a guess, and the operator is the one who has to notice that, so
    the sentence says it rather than reading identically to the sure case.
    """
    via = f"via {choice.name}"
    if check.source == "place-search":
        return f"No stored map link — searched maps instead. Opened {via}."
    if check.source == "agent":
        return f"Stored link was unusable — opened an address read off the receipt {via}."
    return f"Opened in the Windows browser {via}."


def no_launcher_message(check: LinkCheck, choice: LauncherChoice) -> str:
    """The operator's one line when the whole ladder came up empty."""
    return (f"No Windows browser launcher found (tried {', '.join(choice.probed)}). "
            f"Open manually: {check.target}")


# ── Phase 3: hand the link off, detached ──────────────────────────────────────

def launch(run, request: MapOpenRequest, check: LinkCheck, choice: LauncherChoice) -> LaunchResult:
    """Spawn the chosen launcher and return the operator's line.

    Preconditions belong to the two phases above: an unopenable link or an empty
    ladder short-circuits to that phase's message instead of a spawn. Nothing
    here raises — an OSError from the spawn is a message, not a crash.
    """
    if not check.openable:
        return _result(run, LaunchResult(message=unusable_message(check)))
    if not choice.found:
        return _result(run, LaunchResult(message=no_launcher_message(check, choice)))

    spec = next(s for s in ladder(request) if s.name == choice.name)
    argv = spec.build(check.target)
    command = shlex.join(argv)
    if request.dry_run:
        return _result(run, LaunchResult(launcher=choice.name,
                                         message=f"Dry run — would open via {choice.name}: {command}"))

    started_at = now_iso()
    try:
        subprocess.Popen(
            argv,
            cwd=run.repo_root,
            env=operator_env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,      # never block: the exit status is not a signal
        )
    except OSError as error:
        result = LaunchResult(launcher=choice.name,
                              message=f"Could not start {choice.name}: {error}")
    else:
        result = LaunchResult(launched=True, launcher=choice.name,
                              message=opened_message(check, choice))

    run.tracer.event(EventRecord(
        adw_id=run.adw_id,
        phase_id=run.phases[-1].phase_id if run.phases else "",
        type="tool_call",
        name=f"map_open:{choice.name}",
        payload={"command": command, "launched": result.launched, "target": check.target},
        started_at=started_at,
        ended_at=now_iso(),
    ))
    return _result(run, result)


def _result(run, result: LaunchResult) -> LaunchResult:
    run.console.note(result.message)
    return result


# ── Envelope adapter ──────────────────────────────────────────────────────────

def as_envelope(check: LinkCheck, choice: LauncherChoice, result: LaunchResult) -> VerifyOutput:
    """The three deterministic answers, shaped so an agent can be handed them.

    Same adapter idea as `quality.as_envelope` and `changes.as_envelope`: code
    computes, and the result reaches the next agent through the one door every
    handoff uses. This is what an address-resolving agent reads to learn exactly
    which phase said no.
    """
    failures: list[str] = []
    if not check.openable:
        failures.append(f"link rejected: {check.reason} ({check.shown})")
    elif not choice.found:
        failures.append(f"no launcher present (probed {', '.join(choice.probed)})")
    elif not result.launched:
        failures.append(result.message)
    return VerifyOutput(
        status="success" if result.launched else "fail",
        passed=result.launched,
        failures=failures,
        summary=result.message,
        notes_for_next_agent=(f"candidate link: {check.shown}; "
                              f"launcher: {choice.name or 'none'}"),
    )
