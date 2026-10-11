#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Map Open — open an expense's map link in the Windows browser, phase by phase.

The three phases at the core are the three questions
`expense_review/map_opener.py` answers inside one method: is the link openable,
which launcher exists on this box, did the hand-off happen. All three are known
commands, so all three are code.

Two phases sit in front of them for the expense that has no usable link, which
is the common case rather than the exception:

* `derive_place` (code) — most such expenses still say WHERE they were, in their
  own words: "wyoming walmart". That is not an address and never will be, but it
  is exactly what Google Maps' search takes, so a search URL is built from it and
  Maps does the geocoding. Costs nothing and needs no agent.
* `resolve_link` (agent) — only when those words are too illegible to search:
  receipt chicken scratch, OCR noise, a vendor line that is half a reference
  number. Named with `--resolver` (default `locator`, haiku), gated on producing
  a link that passes the same check the stored one failed. Pass
  `--resolver ""` for a strictly code-only run.

Usage:
    uv run adws/adw_map_open.py "<why this map is being opened>" --map-link "https://maps.google.com/..." \
        [--place "wyoming walmart"] [--receipt receipts/2026-02-11.jpg] \
        [--resolver locator] [--dry-run] [--message-out /tmp/line.txt] \
        [--config adws/adw_sssf_config/sssf.config.yaml] [--adw-id a1b2c3d4]

Phases: engineer(request) -> code(validate)
        [-> code(derive_place) [-> <resolver> ] -> code(validate_resolved)]
        -> code(select_launcher) -> code(launch)
"""

import argparse
import sys
from pathlib import Path

from adw_modules import agents, gates, map_open, session, utils
from adw_modules.data_types import (AgentCall, LinkCheck, MapLinkOutput, MapOpenOptions,
                                    MapOpenRequest, PhaseParams, PlaceDerivation)

REQUIRED_AGENTS: list[str] = []          # code-only by default; --resolver adds one
DEFAULT_RESOLVER = "locator"             # the cheap one; the review menu relies on it


def resolver_prompt(options: MapOpenOptions, check: LinkCheck,
                    derivation: PlaceDerivation) -> str:
    """What the resolver is told: the ask, what was rejected, and what was tried.

    Both failures travel with the request on purpose — a resolver that cannot see
    the link that was already refused tends to hand back a variant of it, and one
    that cannot see the query the search step built tends to rebuild it verbatim.
    """
    lines = [options.prompt,
             "",
             f"The stored map_link is not usable: {check.reason} ({check.shown}).",
             f"The expense's own words: {options.place.strip() or '(none)'}",
             f"A mechanical search could not use them: {derivation.reason}",
             "Read the destination out of what is there and build an http(s) map link for it."]
    if options.receipt:
        lines.insert(4, f"Receipt: {options.receipt}")
    return "\n".join(lines)


def main(options: MapOpenOptions) -> int:
    cfg = agents.load_config(options.config)
    agents.validate(cfg, REQUIRED_AGENTS + ([options.resolver] if options.resolver else []))
    run = session.ensure(cfg, options.adw_id)
    request = MapOpenRequest(url=options.map_link, place=options.place, dry_run=options.dry_run)

    with run.phase(PhaseParams(name="request", kind="engineer", owner=run.engineer,
                               description="Record which expense's map is being opened and under what options")) as ph:
        ph.log(input=options.prompt, map_link=options.map_link or "(none)",
               place=options.place or "(none)", resolver=options.resolver or "(code only)",
               dry_run=options.dry_run)

    with run.phase(PhaseParams(name="validate", kind="code", owner="map",
                               description="Decide whether the stored link is safe to hand a browser, before any process is spawned")) as ph:
        check = map_open.validate(run, request)
        ph.log(openable=check.openable, reason=check.reason, target=check.target or "(none)")

    # No usable link, but the expense usually still says where it was. Search for
    # that first: it is code, it is instant, and it is right often enough that the
    # agent below stays the exception.
    if not check.openable:
        with run.phase(PhaseParams(name="derive_place", kind="code", owner="map",
                                   description="Build a maps search from the expense's own words, so a missing link is not a dead end")) as ph:
            derivation = map_open.derive_from_place(run, request)
            ph.log(usable=derivation.usable, query=derivation.query or "(none)",
                   reason=derivation.reason)

        # The one place an agent earns its keep: words no search can use, and a
        # destination only a reader can pull out of them. Absent a resolver the
        # run continues and ends on the operator's "no usable map link" line.
        resolved: MapLinkOutput | None = None
        if not derivation.usable and options.resolver:
            with run.phase(PhaseParams(name="resolve_link", kind="agent", owner=options.resolver, retries=1,
                                       description="Read the destination off the receipt and build a map link neither the stored data nor a search could provide")) as ph:
                resolved = ph.call(AgentCall(output_type=MapLinkOutput,
                                             prompt=resolver_prompt(options, check, derivation),
                                             gates=[gates.map_link_openable]))

        with run.phase(PhaseParams(name="validate_resolved", kind="code", owner="map",
                                   description="Hold the replacement link to the same check the stored one failed, rather than trusting its source")) as ph:
            source = "agent" if resolved else "place-search"
            # A declared miss (`locatable: false`) is a successful phase with no
            # replacement in it — the agent read what was there and there was no
            # place in it. The original check is kept in that case, so the
            # operator's line still names the junk that was actually stored.
            candidate = (resolved.map_link if resolved and resolved.locatable
                         else "" if resolved else derivation.link)
            if candidate:
                request = request.model_copy(update={"url": candidate})
                check = map_open.validate(run, request, source=source)
            else:
                run.console.note(f"map link ({source}): nothing usable was produced")
            ph.log(source=source, replaced=bool(candidate), openable=check.openable,
                   reason=check.reason,
                   address=(resolved.address if resolved else derivation.query) or "(none)",
                   confidence=(resolved.confidence if resolved else "n/a") or "(none)")

    with run.phase(PhaseParams(name="select_launcher", kind="code", owner="map",
                               description="Find the first launcher on the ladder that exists on this box, spawning nothing")) as ph:
        choice = map_open.select_launcher(run, request)
        ph.log(found=choice.found, launcher=choice.name or "(none)", probed=", ".join(choice.probed))

    with run.phase(PhaseParams(name="launch", kind="code", owner="map",
                               description="Hand the link to the chosen launcher detached, and produce the operator's one line")) as ph:
        result = map_open.launch(run, request, check, choice)
        ph.log(launched=result.launched, launcher=result.launcher or "(none)", message=result.message)

    # The caller may be a menu rather than a terminal. It reads this file, so the
    # operator's line is a contract and the console rendering stays formatting.
    if options.message_out:
        Path(options.message_out).write_text(result.message + "\n")

    # A dry run resolved everything a real one would and deliberately spawned
    # nothing, so it is accepted on that basis — not on `launched`, which it
    # honestly reports as false.
    accepted = result.launched or (request.dry_run and check.openable and choice.found)
    return run.finish(accepted=accepted, reason=result.message)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", help="inline text or a path to a prompt file")
    parser.add_argument("--map-link", default="", help="expenses.map_link for the expense under review")
    parser.add_argument("--place", default="", help="the expense's own words for the destination")
    parser.add_argument("--receipt", default="", help="receipt path or text for the resolver agent")
    parser.add_argument("--resolver", default=DEFAULT_RESOLVER,
                        help=f"agent to read the destination when nothing else can "
                             f"(default {DEFAULT_RESOLVER}; pass '' for code only)")
    parser.add_argument("--dry-run", action="store_true", help="resolve a launcher but spawn nothing")
    parser.add_argument("--message-out", default="",
                        help="write the operator's one line here, for a non-terminal caller")
    parser.add_argument("--config", default="adws/adw_sssf_config/sssf.config.yaml")
    parser.add_argument("--adw-id", default=None, help="join or pin an existing session")
    args = parser.parse_args()
    sys.exit(main(MapOpenOptions(prompt=utils.resolve_prompt(args.prompt), map_link=args.map_link,
                                 place=args.place, receipt=args.receipt, resolver=args.resolver,
                                 dry_run=args.dry_run, message_out=args.message_out,
                                 config=args.config, adw_id=args.adw_id)))
