#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "selenium"]
# ///
"""ADW Calculate Distance — drive Google Maps to find the driving distance
from the Gratiot home address to an expense's destination, phase by phase.

Sibling to `adw_map_open.py`, and built the same way: a question that can be
answered in code stays code, and the one part that needs eyes (reading a
destination off receipt chicken scratch) is the one agent phase. The
difference is the browser is not optional here -- `adw_map_open.py` can
answer "is this link openable" without ever touching Chrome, but there is no
way to answer "how far is it" without driving Maps.

Four phases, two more than `adw_map_open.py`'s core three, because finding a
destination worth driving Maps with takes extra steps here:

* `validate_destination` (code) — most expenses already say where they were,
  in their own words ("wyoming walmart"). Legible enough, and that is the
  destination.
* `resolve_destination` (agent) — not legible enough: receipt chicken
  scratch, OCR noise, a vendor line half a reference number. Named with
  `--resolver` (default `locator`, haiku), same contract as
  `adw_map_open.py`'s `resolve_link`. Pass `--resolver ""` for code-only.
  There is deliberately no Google-search step in between, the way
  `adw_map_open.py` has one before ITS agent phase: Google's own search
  results page returns a CAPTCHA ("unusual traffic") to this scripted
  traffic every time, so a code phase that tried it would spend a full
  browser launch to learn nothing before falling through to the resolver
  anyway. Confirmed live, not assumed.
* `calculate` (code, also browser-driven) — type the home address and the
  resolved destination into Maps' own directions box and read the route
  card back, using `WebBrowserManager`
  (`/home/adamsl/selenium-headless/browser_manager/web_browser_manager.py`).
* `locate_nearest` (code, browser-driven) — only runs when `calculate` comes
  back with no route. A destination that is a legible chain name and store
  number ("MISTER CAR WASH #0465 GRAND RAPIDS MI") is exactly the shape that
  passes `validate_destination` yet cannot be geocoded literally; this phase
  searches Maps for it near the origin, reads the nearest match's own
  coordinates, and the run retries `calculate` once with that as the
  destination.

Usage:
    uv run adws/adw_calculate_distance.py "<why this distance is being calculated>" \\
        --place "wyoming walmart" [--map-link "https://maps.google.com/..."] \\
        [--receipt receipts/2026-02-11.jpg] [--origin "<address>"] \\
        [--resolver locator] [--dry-run] [--message-out /tmp/line.txt] \\
        [--config adws/adw_sssf_config/sssf.config.yaml] [--adw-id a1b2c3d4]

Phases: engineer(request) -> code(validate_destination) [-> <resolver> ]
        -> code(calculate) [-> code(locate_nearest) -> code(calculate) ]
"""

import argparse
import sys
from pathlib import Path

from adw_modules import agents, calculate_distance, gates, session, utils
from adw_modules.data_types import (AgentCall, DestinationOutput, DistanceOptions, DistanceResult,
                                    DistanceRequest, PhaseParams)

REQUIRED_AGENTS: list[str] = []          # code-only by default; --resolver adds one
DEFAULT_RESOLVER = "locator"             # the cheap one; the review menu relies on it


def resolver_prompt(options: DistanceOptions, check) -> str:
    """What the resolver is told: the ask, and what the mechanical reading already tried.

    The failure travels with the request on purpose, for the same reason
    `adw_map_open.resolver_prompt` carries its own: a resolver blind to what
    was already tried tends to rebuild the same unusable string.
    """
    lines = [options.prompt,
             "",
             f"The expense's own words: {options.place.strip() or '(none)'}",
             f"A mechanical reading of those words: {check.reason}",
             "Read the destination out of what is there."]
    if options.receipt:
        lines.insert(4, f"Receipt: {options.receipt}")
    return "\n".join(lines)


def main(options: DistanceOptions) -> int:
    cfg = agents.load_config(options.config)
    agents.validate(cfg, REQUIRED_AGENTS + ([options.resolver] if options.resolver else []))
    run = session.ensure(cfg, options.adw_id)
    origin = options.origin.strip() or calculate_distance.HOME_ADDRESS
    request = DistanceRequest(origin=origin, destination=options.place,
                              place=options.place, dry_run=options.dry_run,
                              exclude_addresses=options.exclude_addresses)

    with run.phase(PhaseParams(name="request", kind="engineer", owner=run.engineer,
                               description="Record which expense's distance is being calculated and under what options")) as ph:
        ph.log(input=options.prompt, place=options.place or "(none)",
               map_link=options.map_link or "(none)", origin=origin,
               resolver=options.resolver or "(code only)", dry_run=options.dry_run)

    with run.phase(PhaseParams(name="validate_destination", kind="code", owner="distance",
                               description="Decide whether the expense's own words are already a usable destination")) as ph:
        check = calculate_distance.validate_destination(run, request)
        ph.log(usable=check.usable, candidate=check.candidate or "(none)", reason=check.reason)

    if check.usable:
        # `check.candidate` is `request.destination` with the noise
        # `validate_destination` already judged legible past -- "#0465" and a
        # parenthetical receipt note stay in `options.place`/`request.place`
        # (the resolver prompt still wants the expense's own words verbatim)
        # but have no business reaching Maps' directions box or a search query.
        request = request.model_copy(update={"destination": check.candidate})
    else:
        resolved: DestinationOutput | None = None
        if options.resolver:
            with run.phase(PhaseParams(name="resolve_destination", kind="agent", owner=options.resolver, retries=1,
                                       description="Read the destination off the receipt when a mechanical reading of the expense's own words could not")) as ph:
                resolved = ph.call(AgentCall(output_type=DestinationOutput,
                                             prompt=resolver_prompt(options, check),
                                             gates=[gates.destination_locatable]))

        destination = resolved.address if resolved and resolved.locatable else check.candidate
        request = request.model_copy(update={"destination": destination})

    if request.exclude_addresses:
        # "Next nearest" -- routing the vendor name as typed would just land
        # on the branch already on file, so go straight to the branch search.
        result = DistanceResult(message="Skipped the direct route: looking for a branch other than "
                                        + ", ".join(request.exclude_addresses))
    else:
        with run.phase(PhaseParams(name="calculate", kind="code", owner="distance",
                                   description="Drive Maps' own directions box with both addresses and read the route card it renders")) as ph:
            result = calculate_distance.calculate(run, request)
            ph.log(found=result.found, distance=result.distance_text or "(none)",
                   duration=result.duration_text or "(none)", message=result.message)

    if not result.found and not request.dry_run:
        with run.phase(PhaseParams(name="locate_nearest", kind="code", owner="distance",
                                   description="Search Maps for the nearest branch matching the destination's own words, since no route means the destination may be a chain name rather than an address")) as ph:
            nearest = calculate_distance.locate_nearest(run, request)
            ph.log(found=nearest.found, name=nearest.name or "(none)",
                   address=nearest.address or "(none)", message=nearest.message)

        if not nearest.found and request.exclude_addresses:
            result = DistanceResult(message=nearest.message)

        if nearest.found:
            request = request.model_copy(update={"destination": nearest.destination})
            with run.phase(PhaseParams(name="calculate_nearest", kind="code", owner="distance",
                                       description="Retry the directions box with the nearest branch's own coordinates in place of its unroutable name")) as ph:
                result = calculate_distance.calculate(run, request)
                if result.found:
                    # The directions box was fed "lat,lng"; the caller records the
                    # destination text from this message as the branch's note, so
                    # name the branch in words instead of by coordinates.
                    street = nearest.address.split("\u00b7")[-1].strip()
                    label = ", ".join(part for part in (nearest.name, street) if part)
                    if label:
                        result = result.model_copy(update={
                            "message": result.message.replace(nearest.destination, label)})
                ph.log(found=result.found, distance=result.distance_text or "(none)",
                       duration=result.duration_text or "(none)", message=result.message)

    if not result.found and not request.dry_run and not request.exclude_addresses:
        # Neither the directions box nor a Maps search near home resolved it:
        # ask SerpAPI's Google Search for the business's street address.
        with run.phase(PhaseParams(name="search_address", kind="code", owner="distance",
                                   description="Ask SerpAPI's Google Search for the destination's own street address, the last resort after Maps itself found nothing")) as ph:
            searched = calculate_distance.search_address(run, request)
            ph.log(found=searched.found, name=searched.name or "(none)",
                   address=searched.address or "(none)", message=searched.message)

        if searched.found:
            request = request.model_copy(update={"destination": searched.address})
            with run.phase(PhaseParams(name="calculate_searched", kind="code", owner="distance",
                                       description="Retry the directions box with the street address SerpAPI found")) as ph:
                result = calculate_distance.calculate(run, request)
                ph.log(found=result.found, distance=result.distance_text or "(none)",
                       duration=result.duration_text or "(none)", message=result.message)

    # The caller may be a menu rather than a terminal. It reads this file, so
    # the operator's line is a contract and the console rendering stays
    # formatting -- same rule as adw_map_open.py.
    if options.message_out:
        Path(options.message_out).write_text(result.message + "\n")

    # Only when the row had no map_link of its own: store the route just
    # calculated so the next review of this expense can open it directly.
    if options.map_link_out and result.found and not options.map_link.strip() and request.destination:
        Path(options.map_link_out).write_text(
            calculate_distance.build_map_link(origin, request.destination) + "\n")

    accepted = result.found or (request.dry_run and bool(request.destination))
    return run.finish(accepted=accepted, reason=result.message)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", help="inline text or a path to a prompt file")
    parser.add_argument("--map-link", default="", help="expenses.map_link, read for a destination hint")
    parser.add_argument("--place", default="", help="the expense's own words for the destination")
    parser.add_argument("--receipt", default="", help="receipt path or text for the resolver agent")
    parser.add_argument("--origin", default="", help="override the Gratiot home address as the trip's start")
    parser.add_argument("--resolver", default=DEFAULT_RESOLVER,
                        help=f"agent to read the destination when nothing else can "
                             f"(default {DEFAULT_RESOLVER}; pass '' for code only)")
    parser.add_argument("--dry-run", action="store_true", help="resolve a destination but never open Chrome")
    parser.add_argument("--message-out", default="",
                        help="write the operator's one line here, for a non-terminal caller")
    parser.add_argument("--map-link-out", default="",
                        help="write a newly-derived map_link here (only when --map-link was empty)")
    parser.add_argument("--exclude-address", action="append", default=[], dest="exclude_address",
                        help="a branch already on file to skip (repeatable); finds the next nearest instead")
    parser.add_argument("--config", default="adws/adw_sssf_config/sssf.config.yaml")
    parser.add_argument("--adw-id", default=None, help="join or pin an existing session")
    args = parser.parse_args()
    sys.exit(main(DistanceOptions(prompt=utils.resolve_prompt(args.prompt), map_link=args.map_link,
                                  place=args.place, receipt=args.receipt, origin=args.origin,
                                  resolver=args.resolver, dry_run=args.dry_run,
                                  message_out=args.message_out, map_link_out=args.map_link_out, exclude_addresses=args.exclude_address, config=args.config,
                                  adw_id=args.adw_id)))
