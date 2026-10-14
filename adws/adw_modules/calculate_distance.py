"""Calculate the driving distance from the Gratiot home address to an
expense's destination, one phase per question -- the same split `map_open.py`
already uses for opening a map.

Three questions, three callables:

* `validate_destination` (code) -- is there already a destination worth
  driving Maps with, read straight off the expense's own words? When this
  comes back unusable, the ADW script goes straight to its `--resolver`
  agent rather than trying a code-only middle step: Google's own search
  results page returns a CAPTCHA ("unusual traffic") to this scripted
  traffic every time it was tried live, so there is no cheap mechanical
  fallback worth having here, unlike `map_open.py`'s `derive_from_place`.
* `calculate` (code, browser-driven) -- type both addresses into Maps' own
  directions box and read the route card back, using `WebBrowserManager`
  (`/home/adamsl/selenium-headless/browser_manager/web_browser_manager.py`)
  rather than the documented `/maps/dir/?origin=...&destination=...` URL the
  separate maps-distance-tool service uses -- that tool already exists;
  this ADW exists to drive the UI itself, per spec.
* `locate_nearest` (code, browser-driven) -- when `calculate` comes back
  with no route, the destination may be a chain's name and store number
  rather than an address at all ("MISTER CAR WASH #0465 GRAND RAPIDS MI"
  is not geocodable literally, but it names a *kind* of place to find near
  home). This drives Maps' *search* results instead of its directions box,
  reads the nearest match's coordinates off its own result card, and hands
  those back as a destination `calculate` can retry with. Unlike the
  Google-search CAPTCHA above, `maps.google.com/search/` is the same Maps
  surface `calculate` already drives without issue -- confirmed live, not
  assumed.

Same two invariants as `map_open.py`, because they are the right invariants
for anything that drives a browser out of a menu action:

* **Never raise.** A flaky scrape or a page Google reshuffled becomes a
  message, never a crash -- the PHASE still succeeds, because looking and
  finding nothing is a job well done.
* **Always quit the driver.** Each phase launches its own
  `WebBrowserManager` and tears it down in a `finally`, so a scrape that
  throws halfway through does not leave a Chrome process behind.

Selector note: Google's class names are minified and drift over time. If
`calculate()` starts returning "no route" for addresses that plainly have
one, check the selectors below against a fresh `maps.google.com/dir/` page
first -- see the maps-driving-distance skill for the same caveat about the
sibling HTTP service's selectors.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

from . import map_open
from .data_types import (DestinationCheck, DistanceRequest, DistanceResult,
                         NearestLocationResult, SearchAddressResult)
from urllib.parse import parse_qs, unquote_plus

#: The nonprofit's operators' address -- same value as
#: `tools/receipt_scanning_tools/receipt_parsing_tools/mileage.py:HOME_ADDRESS`
#: in the rol_finances repo. Duplicated rather than imported: that module sits
#: behind a package `__init__.py` chain this ADW has no reason to load.
HOME_ADDRESS = "1854 Gratiot St NW, Grand Rapids, MI 49504"

#: Where `web_browser_manager.py` lives -- the module itself, not the repo
#: root, since it is imported bare (`from web_browser_manager import ...`)
#: rather than as `browser_manager.web_browser_manager`. Not a package on
#: `sys.path` anywhere else, so it is added here, once, the same way
#: `expense_review/app.py` adds `smart_menu` to its own path.
WEB_BROWSER_MANAGER_DIR = Path.home() / "selenium-headless" / "browser_manager"
if str(WEB_BROWSER_MANAGER_DIR) not in sys.path:
    sys.path.insert(0, str(WEB_BROWSER_MANAGER_DIR))

#: "ace" alone is not a destination; "walmart" is. Same bar as
#: `map_open.MIN_PLACE_LETTERS`, kept separate so the two can diverge later
#: without one edit silently changing the other's behavior.
MIN_DESTINATION_LETTERS = 4

#: A free-text operator note tacked onto the destination with a dash, e.g.
#: "Walgreens - There was what looked like a cash back.  May need to verify
#: this." -- confirmed live on expense 473, where the whole sentence was
#: handed to Maps as the destination both for `calculate`'s directions box
#: and `locate_nearest`'s search, and matched nothing either way. Told apart
#: from a genuine address continuation after a dash ("MISTER CAR WASH - 1234
#: Division Ave") by the absence of any digit: a note is prose, an address
#: has a house number.
_PROSE_ASIDE = re.compile(r"\s-\s+([^-]*[A-Za-z][^-]*)$")

_WORD = re.compile(r"[A-Za-z]{3,}")


def _drop_prose_note(query: str) -> str:
    match = _PROSE_ASIDE.search(query)
    if not match:
        return query
    note = match.group(1)
    if re.search(r"\d", note) or len(note.split()) < 3:
        return query
    # A branch descriptor that just repeats the brand name -- "Mister -
    # Mister Leonard car wash" -- is not a note at all, confirmed live on
    # expense 468: stripping it left the bare word "Mister" as the Maps
    # destination, which geocoded to an unrelated business 8.2 mi away
    # instead of the real branch 0.8 mi away that the full description
    # would have named. A genuine aside ("Walgreens - There was what looked
    # like a cash back") shares no word with the brand before the dash; a
    # branch descriptor does.
    before = {word.lower() for word in _WORD.findall(query[:match.start()])}
    after = {word.lower() for word in _WORD.findall(note)}
    if before & after:
        return query
    return query[:match.start()].strip(" ,.-")


def _browser():
    """`WebBrowserManager`, pointed at the WSLg display instead of headless.

    Confirmed directly on this box: `google-chrome --headless=new` hangs and
    never returns, while a plain visible launch against the WSLg display
    (`DISPLAY=:0`, already set in this user's session) opens and navigates in
    under a second. Headless is not a safe default here -- this overrides
    `initialize_driver` to drop it and keep only the two flags Chrome still
    needs to run as this user in a container (`--no-sandbox`,
    `--disable-dev-shm-usage`; see the sibling maps-distance-tool's
    `selenium_provider.py`, which runs the same way on desktop-shdbati for
    the identical reason -- a visible WSLg window, not a headless one).
    Overriding the one method, not the shared file -- everything else
    (navigate_to_page, find_elements, quit, ...) is used exactly as written
    there.

    A real window flashing on screen during a review session is the visible
    cost of this: if that becomes a problem, --headless=new can be tried
    again once Chrome's headless mode stops hanging on this box.
    """
    from selenium import webdriver  # noqa: PLC0415 - browser cost paid only here
    from selenium.webdriver.chrome.options import Options  # noqa: PLC0415
    from web_browser_manager import WebBrowserManager  # noqa: PLC0415

    class _VisibleBrowser( WebBrowserManager ):
        def initialize_driver( self ) -> webdriver.Chrome:
            chrome_options = Options()
            chrome_options.add_argument( "--no-sandbox" )
            chrome_options.add_argument( "--disable-dev-shm-usage" )
            chrome_options.add_argument( "--window-size=1280,1024" )
            return webdriver.Chrome( options=chrome_options )

    return _VisibleBrowser()


# ── Phase 0: a map_link already on the row beats re-deriving a destination ──

def address_from_map_link(url: str) -> str:
    """Pull the destination straight out of a stored `expenses.map_link`.

    Confirmed against live rows: the map-distance tool writes
    `maps/dir/?api=1&origin=...&destination=...&travelmode=driving`, and this
    ADW's own write-back (`build_map_link` below) writes the identical shape.
    Falls back to the older path-style `maps/search/<query>` link
    `map_open.py` builds when no destination/query parameter is present.
    Empty string -- not an exception -- when the link is missing or carries
    neither, so a junk or foreign-shaped link just falls through to the
    existing place-based chain instead of blocking it.
    """
    if not url:
        return ""
    parsed = urllib.parse.urlparse(url)
    query = parse_qs(parsed.query)
    for key in ("destination", "query", "q"):
        values = query.get(key)
        if values and values[0].strip():
            return unquote_plus(values[0]).strip()
    if parsed.path.startswith("/maps/search/"):
        candidate = unquote_plus(parsed.path[len("/maps/search/"):]).strip()
        if candidate:
            return candidate
    return ""


def build_map_link(origin: str, destination: str) -> str:
    """The same `maps/dir/?api=1&...` shape the map-distance tool already
    writes into `expenses.map_link` -- so a destination this ADW resolves is
    stored exactly as if that tool had found it, and `address_from_map_link`
    can read it straight back on a future run with no extra case to handle.
    """
    params = urllib.parse.urlencode({
        "api": "1", "origin": origin, "destination": destination,
        "travelmode": "driving",
    })
    return f"https://www.google.com/maps/dir/?{params}"


# ── Phase 1: is there already a destination worth driving Maps with ─────────

def validate_destination(run, request: DistanceRequest) -> DestinationCheck:
    """Normalize the expense's own words and judge whether they are legible.

    Reuses `map_open.place_query` rather than re-deriving the same cleanup
    rule -- "wyoming walmart" wants the same treatment whether the next step
    is a maps search link or a maps directions box.
    """
    query = _drop_prose_note(map_open.place_query(request.destination or request.place))
    letters = sum(ch.isalpha() for ch in query)
    if not query:
        check = DestinationCheck(reason="the expense names no destination")
    elif letters < MIN_DESTINATION_LETTERS:
        check = DestinationCheck(candidate=query,
                                 reason=f"{query!r} is not legible as a destination")
    else:
        check = DestinationCheck(candidate=query, usable=True,
                                 reason=f"{query!r} is legible as a destination")
    run.console.note(f"destination: {check.reason}")
    return check


#: A house number followed by a street-suffix word -- the one shape a bare
#: brand name + store/reference number never has. Deliberately looser than
#: `_STREET_ADDRESS` below (no required city/state/zip): this only needs to
#: tell "1234 Division Ave" apart from "MARATHON 71613 GRAND RAPIDS MI", not
#: validate a full address.
_ADDRESS_HINT = re.compile(
    r"\d+\s+[\w.'-]+(?:\s+[\w.'-]+){0,4}\s+"
    r"(?:St|Ave|Rd|Dr|Blvd|Ct|Ln|Pkwy|Hwy|Way|Pl|Cir|Ter)\b",
    re.IGNORECASE)


def looks_like_address(destination: str) -> bool:
    """Whether `destination` already names a real street address, rather
    than a brand name and a store/reference number ("MARATHON 71613 GRAND
    RAPIDS MI").

    Confirmed live on expense 818: Maps' directions box geocoded that exact
    string to *some* nearby Marathon station and returned a plausible 8.5 mi
    route, so `calculate()` reported `found=True` and the run never reached
    `locate_nearest` -- the phase that exists specifically to pick the right
    branch out of several same-named candidates near home (see its own
    "Marathon Gas" collision note on expense 1500). A `calculate()` hit for
    an address-shaped destination can be trusted on its own; a hit for a
    bare brand+number cannot, because Maps' autocomplete has no reason to
    prefer the branch nearest home over any other branch within a plausible
    distance of it. Callers should route the latter through `locate_nearest`
    even when `calculate()` already "found" something.
    """
    return bool(_ADDRESS_HINT.search(destination or ""))


# ── Phase 2: drive Maps itself, and read the answer back ────────────────────

#: The directions box's own two fields -- confirmed live against
#: maps.google.com/dir/. `input.tactile-searchbox-input` (tried first) is the
#: Maps HOMEPAGE's single search box; it is not present on the directions
#: page at all, which is why the original selector always came back empty.
DIRECTIONS_FIELDS = "input.ZBTq6e"

#: Every expense this ADW is ever asked about is a local trip -- gas,
#: hardware stores, doctor visits -- all near `HOME_ADDRESS`. Confirmed live
#: on expense 1500 ("LEO GAS AND FOOD LLC", no street address): typed as-is
#: into the directions box, Maps geocoded it to a same-named branch 127 mi
#: away while an actual Leo Gas & Food sits under 10 mi from home. The
#: directions box has no "near <origin>" bias the way `locate_nearest`'s
#: search does, so a bare business name can match any branch nationwide.
#: Treating a route past this bound as if no route were found at all routes
#: the call into that existing nearest-branch search instead of trusting it.
MAX_PLAUSIBLE_MILES = 50.0

_DISTANCE_MILES = re.compile(r"([\d.]+)\s*mi(?:les)?\b", re.IGNORECASE)


def _parse_miles(distance_text: str) -> float | None:
    match = _DISTANCE_MILES.search(distance_text)
    return float(match.group(1)) if match else None


def calculate(run, request: DistanceRequest) -> DistanceResult:
    """Type both addresses into Maps' own directions box, then read the card.

    Deliberately UI-driven rather than built as a `/maps/dir/?origin=...
    &destination=...` URL -- that shortcut is what the separate
    maps-distance-tool HTTP service already does. This phase exists to
    exercise `WebBrowserManager` the way a person operating Maps would.
    """
    origin = request.origin or HOME_ADDRESS
    if request.dry_run:
        message = f"Dry run -- would calculate {origin} -> {request.destination}"
        run.console.note( message )
        return DistanceResult( message=message )

    try:
        from selenium.webdriver.common.keys import Keys  # noqa: PLC0415
        manager = _browser()
    except Exception as error:  # noqa: BLE001 - a missing driver must not crash the run
        message = f"Could not start the browser: {error}"
        run.console.note( message )
        return DistanceResult( message=message )

    try:
        manager.navigate_to_page( "https://www.google.com/maps/dir/" )
        time.sleep( 4 )  # the directions box itself renders asynchronously
        fields = manager.find_elements( DIRECTIONS_FIELDS )
        if len( fields ) < 2:
            return DistanceResult( message="Could not find the Maps directions fields." )
        origin_field, destination_field = fields[0], fields[1]
        origin_field.send_keys( origin )
        time.sleep( 1 )  # Maps' own autocomplete needs a beat between fields
        destination_field.send_keys( request.destination )
        time.sleep( 1 )
        destination_field.send_keys( Keys.ENTER )

        # A fixed sleep here raced the route card often enough to be a real
        # flake, not a theoretical one: the same input came back "no route"
        # about one run in five with `sleep(4)` and a single scrape attempt,
        # then succeeded on an immediate retry. Polling for up to 10s instead
        # of gambling on one fixed wait turns that into a bounded wait.
        distance_elements: list = []
        duration_elements: list = []
        for _ in range( 20 ):
            distance_elements = manager.find_elements( "div[data-trip-index='0'] .ivN21e" )
            if distance_elements:
                duration_elements = manager.find_elements( "div[data-trip-index='0'] .Fk3sm" )
                break
            time.sleep( 0.5 )
        distance_text = distance_elements[0].text.strip() if distance_elements else ""
        duration_text = duration_elements[0].text.strip() if duration_elements else ""
    except Exception as error:  # noqa: BLE001 - a broken scrape is a message, not a crash
        return DistanceResult( message=f"Could not calculate distance: {error}" )
    finally:
        manager.quit()

    if not distance_text:
        message = f"Maps returned no route from {origin} to {request.destination}."
        run.console.note( message )
        return DistanceResult( message=message )

    miles = _parse_miles( distance_text )
    if miles is not None and miles > MAX_PLAUSIBLE_MILES:
        message = (f"Discarding implausible route from {origin} to "
                   f"{request.destination}: {distance_text} is over "
                   f"{MAX_PLAUSIBLE_MILES:g} mi -- likely the wrong branch.")
        run.console.note( message )
        return DistanceResult( message=message )

    message = f"{origin} to {request.destination}: {distance_text}"
    if duration_text:
        message += f" ({duration_text})"
    run.console.note( message )
    return DistanceResult( distance_text=distance_text, duration_text=duration_text,
                           found=True, message=message )


# ── Phase 3: no route as typed -- find the nearest actual branch ────────────

#: Confirmed live against maps.google.com/search/: each result is one
#: `role="article"` card; its name sits behind the card's own link, whose
#: href carries the place's coordinates (`!3d<lat>!4d<lng>`) -- exact, and
#: needs no city/state stitched onto a partial address line to be usable.
SEARCH_RESULT_CARD = ".Nv2PK"
RESULT_LINK = "a.hfpxzc"
_LAT_LNG = re.compile(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)")

#: A full-address search (no result list) redirects straight to a place page
#: whose URL centers on the address, `.../@<lat>,<lng>,<zoom>z/...` -- used to
#: geocode `origin` the same way, with the same browser, before judging which
#: search-result card is actually nearest to it.
_URL_LAT_LNG = re.compile(r"@(-?\d+\.\d+),(-?\d+\.\d+)")

#: A chain's brand name, e.g. "MISTER CAR WASH" out of "MISTER CAR WASH 0465
#: GRAND RAPIDS MI" -- the words before the first digit, which is where a
#: store number starts. Fallback only, when the categorizer's own vendor key
#: (below) does not recognize the destination at all.
_BRAND_NAME = re.compile(r"^([A-Za-z&' ]+)")

#: The categorizer's vendor keys already carry a curated, deduplicated name
#: for exactly this kind of destination -- "LEO GAS AND FOOD LLC" resolves to
#: vendor_key "leo_gas", the same key that also categorizes "BP#2836900LEO GAS
#: & GRAND RAPIDS MI" (a real BP-branded station whose own signage reads "Leo
#: Gas", not a chain called "Leo"). Reading the key off that file rather than
#: guessing from the raw destination text is what the brand filter needs:
#: single-word overlap on "gas" alone matched an unrelated "Marathon Gas"
#: result card before this, confirmed live on expense 1500.
VENDOR_YAML_PATH = (Path.home() / "rol_finances" / "tools" / "categorizer"
                    / "vendor_category.yaml")

#: `vendor_category.yaml` is not a single well-formed YAML document (its
#: "# ///// Vendor Key: ... /////" sections repeat the top-level `aliases:`/
#: `entries:` keys throughout the file -- `VendorCategoryStore` in the
#: rol_finances repo has its own sectioned parser for exactly that reason).
#: Only the (vendor_key, regex) pairs are needed here, so they are pulled out
#: directly rather than pulling in that parser and its own dependency chain
#: across repos.
_VENDOR_ALIAS_PATTERN = re.compile(
    r'vendor_key:\s*"([^"]+)"\s*\n\s*regex:\s*"([^"]+)"')

_vendor_aliases_cache: list[tuple[str, "re.Pattern[str]"]] | None = None


def _vendor_aliases() -> list[tuple[str, "re.Pattern[str]"]]:
    global _vendor_aliases_cache
    if _vendor_aliases_cache is None:
        try:
            text = VENDOR_YAML_PATH.read_text(encoding="utf-8")
        except OSError:
            text = ""
        aliases = []
        for vendor_key, pattern in _VENDOR_ALIAS_PATTERN.findall(text):
            try:
                aliases.append((vendor_key, re.compile(pattern, re.IGNORECASE)))
            except re.error:
                continue  # a malformed regex in the yaml must not break distance lookups
        _vendor_aliases_cache = aliases
    return _vendor_aliases_cache


def _normalize_for_vendor_match(text: str) -> str:
    """Same normalization `VendorCategoryStore` uses before testing a regex
    against free text -- lowercased, non-alphanumeric runs collapsed to one
    underscore."""
    cleaned = re.sub(r"[^a-z0-9]+", "_", text.lower())
    return re.sub(r"_+", "_", cleaned).strip("_")


def _vendor_key(destination: str) -> str | None:
    """The categorizer's own vendor key for this destination, or `None` when
    no alias matches."""
    normalized = _normalize_for_vendor_match(destination)
    best: str | None = None
    for vendor_key, pattern in _vendor_aliases():
        if pattern.search(normalized) and (best is None or len(vendor_key) > len(best)):
            best = vendor_key
    return best


def _vendor_key_words(destination: str) -> set[str] | None:
    """The vendor key's own words when the categorizer recognizes this
    destination, so the brand filter can require *all* of them -- not just
    any one -- to show up on a result card. `None` when no alias matches, so
    the caller can fall back to the looser guess."""
    best = _vendor_key(destination)
    if best is None:
        return None
    words = {word for word in best.split("_") if len(word) >= 3 and not word.isdigit()}
    return words or None


#: Road/direction words that show up in a *location* phrase, not a brand
#: name -- the full-word form of the same street-suffix vocabulary
#: `_STREET_ADDRESS` already matches abbreviated ("Dr", "Ave", ...). A real
#: chain name is never made entirely of these.
_ROAD_WORDS = frozenset({
    "street", "avenue", "road", "drive", "boulevard", "court", "lane",
    "way", "place", "circle", "terrace", "highway", "parkway", "north",
    "south", "east", "west", "lake",
})


def _brand_words(destination: str) -> set[str]:
    """Words a real result card for this destination ought to carry.

    `_vendor_key_words` is trusted outright -- it comes from the
    categorizer's own alias table, keyed to the raw bank text, not a guess.
    The `_BRAND_NAME` regex is only a guess, and it fails one specific way:
    confirmed live on expense 479, "Lake MI Drive BP" has no vendor_key
    match (the categorizer's aliases are keyed to the raw bank text
    "BP#9332735LAKE MICHI...", not this human-cleaned description), no
    digit to stop `_BRAND_NAME` early, and its real brand token "BP" is too
    short to survive the 3-letter word filter below -- so what is left is
    {"lake", "drive"}, pure road vocabulary describing WHERE the place is,
    not WHAT it is. Filtering candidate cards on that then rejects the real
    station's own Maps card (signed and listed only as "bp", carrying
    neither word) while `search_address`'s SerpAPI title could carry both
    without being the right business at all. When every surviving word is
    road vocabulary, there is no real brand signal here, so this returns no
    words rather than a wrong, overconfident set -- same principle as
    `locate_nearest`'s own "no filter beats a wrong one," applied one step
    earlier, and narrow enough that a real single-word brand with no store
    number ("Meijer", "Costco") still filters exactly as before.
    """
    vendor_words = _vendor_key_words(destination)
    if vendor_words:
        return vendor_words
    match = _BRAND_NAME.match(destination.strip())
    brand = match.group(1) if match else destination
    words = {word.lower() for word in re.findall(r"[A-Za-z]{3,}", brand)}
    return set() if words and words <= _ROAD_WORDS else words


def _location_words(destination: str) -> set[str]:
    """The road words `_brand_words` discards, repurposed as an address
    filter instead of a name filter.

    Confirmed necessary live on expense 479's "Lake MI Drive BP": with no
    name filter at all, `locate_nearest` happily returned whichever "bp"
    card sat nearest home -- 1639 Leonard St NW, the real, different
    station already on record as `leo_gas` -- instead of the one the
    description actually named, 2035 Lake Michigan Dr. Two distinct BP
    stations near home means "nearest card with a plausible name" alone
    cannot tell them apart; the street name in the description can, but
    only against a candidate's own ADDRESS line, never its business name
    (a station is never signed "Lake Michigan Drive").
    """
    return ({word.lower() for word in re.findall(r"[A-Za-z]{3,}", destination)}
            & _ROAD_WORDS)


#: Businesses whose own Maps listing carries none of their billing
#: descriptor's words at all -- no brand-word search, however phrased, can
#: ever match them there. "LEO GAS AND FOOD LLC"/"BP#2836900LEO GAS & GRAND
#: RAPIDS MI" (vendor_key leo_gas / bp_2836900_leo_gas) is signed and listed
#: on Maps only as "bp" -- confirmed live via SerpAPI's google_maps engine
#: against the known address. A SerpAPI *web* search used to surface a
#: third-party directory page that happened to name it "LEO'S GAS N FOOD";
#: that was never anything but Google's organic ranking on the day it was
#: checked, and it had already stopped working the next time this ran.
#: Known street addresses are the only durable fix for this class of
#: destination -- keyed by vendor_key so any future alias for the same
#: business reuses the same entry.
_KNOWN_ADDRESSES: dict[str, str] = {
    "leo_gas": "1639 Leonard St NW, Grand Rapids, MI 49504",
    "bp_2836900_leo_gas": "1639 Leonard St NW, Grand Rapids, MI 49504",
}


def _normalize_address(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _is_excluded(address: str, exclude_addresses: list[str]) -> bool:
    """Whether `address` is close enough to one of `exclude_addresses` to be
    the same location, so `locate_nearest` can pass over branches already on
    file instead of reporting one of them back as "the next nearest."

    Compared on normalized substring overlap rather than exact equality --
    `exclude_addresses` entries come from vendor_distance.txt notes, which
    are often partial ("925 Leon [handwritten]") rather than a full address
    with city/state/zip the way a Maps result card's address line is.
    """
    normalized = _normalize_address(address)
    if not normalized:
        return False
    for excluded in exclude_addresses:
        other = _normalize_address(excluded)
        if other and (other in normalized or normalized in other):
            return True
    return False


def _haversine_miles(point_a: tuple[float, float], point_b: tuple[float, float]) -> float:
    lat1, lng1, lat2, lng2 = (radians(value) for value in (*point_a, *point_b))
    delta_lat, delta_lng = lat2 - lat1, lng2 - lng1
    a = sin(delta_lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(delta_lng / 2) ** 2
    return 2 * 3958.8 * asin(sqrt(a))


def _geocode(manager, address: str) -> tuple[float, float] | None:
    """Where `address` itself sits, read off the place page Maps redirects a
    full-address search to -- the same technique `locate_nearest` already
    uses for a result card, aimed at the origin instead of a candidate."""
    manager.navigate_to_page(f"https://www.google.com/maps/search/{urllib.parse.quote(address)}")
    for _ in range(20):
        match = _URL_LAT_LNG.search(manager.driver.current_url)
        if match:
            return float(match.group(1)), float(match.group(2))
        time.sleep(0.5)
    return None


def locate_nearest(run, request: DistanceRequest) -> NearestLocationResult:
    """Search Maps for the destination near the origin, and read back
    whichever matching result card is actually closest to the origin.

    Called only as a fallback after `calculate()` reports no route -- most
    destinations are already specific enough that this extra browser launch
    would be pure cost. A chain name alone is the case it exists for.

    Maps' own "near <address>" query bias is necessary but not sufficient --
    confirmed live, a five-result list for "MISTER CAR WASH ... near 1854
    Gratiot" put a sponsored ad for an unrelated detailing shop first and the
    branch nearest the origin (0.9 mi away) third. So every card is read, the
    ones that are not plausibly the same chain are thrown out by brand-word
    overlap, and whatever is left is ranked by straight-line distance to the
    origin's own geocoded position -- not by Maps' result order.
    """
    origin = request.origin or HOME_ADDRESS
    if request.dry_run:
        message = f"Dry run -- would search Maps for {request.destination!r} near {origin}"
        run.console.note(message)
        return NearestLocationResult(message=message)

    try:
        manager = _browser()
    except Exception as error:  # noqa: BLE001 - a missing driver must not crash the run
        message = f"Could not start the browser: {error}"
        run.console.note(message)
        return NearestLocationResult(message=message)

    try:
        from selenium.webdriver.common.by import By  # noqa: PLC0415

        origin_point = _geocode(manager, origin)

        query = f"{request.destination} near {origin}"
        manager.navigate_to_page(f"https://www.google.com/maps/search/{urllib.parse.quote(query)}")

        cards: list = []
        for _ in range(20):
            cards = manager.find_elements(SEARCH_RESULT_CARD)
            if cards:
                break
            time.sleep(0.5)
        if not cards:
            message = f"Maps found no nearby match for {request.destination!r}."
            run.console.note(message)
            return NearestLocationResult(message=message)

        brand_words = _brand_words(request.destination)
        #: Only consulted when there is no brand-name signal at all --
        #: see `_location_words`'s docstring for why it filters addresses,
        #: never names.
        location_words = set() if brand_words else _location_words(request.destination)
        candidates = []  # (distance_or_None, name, address, lat, lng)
        for card in cards:
            try:
                href = card.find_element(By.CSS_SELECTOR, RESULT_LINK).get_attribute("href") or ""
            except Exception:  # noqa: BLE001 - one bad card must not sink the rest
                continue
            coordinates = _LAT_LNG.search(href)
            if not coordinates:
                continue
            lines = [line.strip() for line in card.text.splitlines() if line.strip()]
            name = lines[0] if lines else request.destination
            card_words = {word.lower() for word in re.findall(r"[A-Za-z]{3,}", name)}
            if brand_words and not brand_words <= card_words:
                continue  # a different business, or only a generic word in common
            address = next((line.split(" · ", 1)[-1] for line in lines if " · " in line), "")
            if location_words:
                address_words = {word.lower() for word in re.findall(r"[A-Za-z]{3,}", address)}
                if not location_words <= address_words:
                    continue  # right-ish name, wrong street -- not the branch named
            if request.exclude_addresses and _is_excluded(address, request.exclude_addresses):
                continue  # already on file -- the caller wants a different branch
            lat, lng = float(coordinates.group(1)), float(coordinates.group(2))
            distance = _haversine_miles(origin_point, (lat, lng)) if origin_point else None
            candidates.append((distance, name, address, lat, lng))

        if not candidates:
            message = f"Maps found no nearby match for {request.destination!r}."
            run.console.note(message)
            return NearestLocationResult(message=message)

        # Candidates lacking a distance (origin could not be geocoded) sort
        # last rather than crashing the comparison -- Maps' own result order
        # is the only thing left to prefer at that point.
        candidates.sort(key=lambda row: row[0] if row[0] is not None else float("inf"))
        distance, name, address, lat, lng = candidates[0]
        destination = f"{lat},{lng}"
        near = f" ({distance:.1f} mi from the origin)" if distance is not None else ""
        message = f"Nearest match for {request.destination!r}: {name}{near}" + (f", {address}" if address else "")
        run.console.note(message)
        return NearestLocationResult(found=True, name=name, address=address,
                                     destination=destination, message=message)
    except Exception as error:  # noqa: BLE001 - a broken scrape is a message, not a crash
        message = f"Could not search Maps for a nearby match: {error}"
        run.console.note(message)
        return NearestLocationResult(message=message)
    finally:
        manager.quit()


# ── Phase 4: Maps itself found nothing -- ask a real search engine ──────────

#: Read from this machine's `~/.bashrc` (`export SERP_API_KEY=...`), not a
#: hardcoded literal -- confirmed present there, not anywhere in Letta's own
#: config as EG originally believed. A subprocess only sees this if its
#: parent shell actually sourced `.bashrc` (an interactive or login shell);
#: a run that gets here with no key set just logs that and falls through
#: rather than crashing, the same "never raise" rule every phase above keeps.
SERP_API_KEY_ENV = "SERP_API_KEY"
SERP_API_URL = "https://serpapi.com/search.json"
SERP_API_TIMEOUT = 15

#: `urllib`'s TLS handshake to serpapi.com hung past a 10s timeout twice in
#: a row live, while a plain `curl` against the identical URL moments later
#: answered in ~3s both times -- a one-off connection flake, not a slow
#: server. One retry absorbs that without this phase needing its own
#: backoff/circuit-breaker machinery.
SERP_API_ATTEMPTS = 2

#: Biases SerpAPI's Google results toward this area the same way
#: `locate_nearest`'s "near <origin>" query biases Maps -- otherwise a
#: same-named business a thousand miles away can outrank the real one,
#: exactly the 127-mi-branch problem this whole fallback chain exists for.
#: This alone was not enough, confirmed live: a bare "LEO GAS AND FOOD LLC"
#: query returned no local_results/knowledge_graph at all, only unrelated
#: nationwide "Leo's" businesses in organic_results. Appending the city and
#: state to the QUERY TEXT itself (not just this location-bias parameter)
#: is what actually surfaced the real listing -- same result EG got typing
#: the name into Google by hand.
SERP_API_LOCATION = "Grand Rapids, Michigan, United States"
SERP_API_QUERY_LOCALITY = "Grand Rapids, MI"

#: Pulled out of an origin like "1854 Gratiot St NW, Grand Rapids, MI 49504"
#: when `request.origin` overrides the default -- falls back to the Grand
#: Rapids constant above when the origin's shape does not match.
_ORIGIN_LOCALITY = re.compile(r",\s*([A-Za-z .]+,\s*[A-Z]{2})\s*\d{5}")

#: A full US street address -- house number, street name, optional
#: directional suffix, city, state, zip -- confirmed against SerpAPI's own
#: organic-result titles, which carry exactly this shape for a local
#: business ("LEO'S GAS N FOOD - Updated October 2026 | 1639 Leonard St NW
#: Grand Rapids, MI 49504"). Loose on the street-name words so it tolerates
#: whatever sits between the house number and the suffix, strict on the
#: suffix/state/zip shape that marks where an address actually starts.
_STREET_ADDRESS = re.compile(
    r"\d+\s+[\w.'-]+(?:\s+[\w.'-]+){0,4}\s+"
    r"(?:St|Ave|Rd|Dr|Blvd|Ct|Ln|Pkwy|Hwy|Way|Pl|Cir|Ter)\.?"
    r"\s*(?:NW|NE|SW|SE|N|S|E|W)?,?\s+[A-Za-z .]+,\s*[A-Z]{2},?\s*\d{5}",
    re.IGNORECASE)


def _origin_locality(origin: str) -> str:
    match = _ORIGIN_LOCALITY.search(origin)
    return match.group(1) if match else SERP_API_QUERY_LOCALITY


def _address_from_serpapi(data: dict, brand_words: set[str]) -> tuple[str, str]:
    """Pull a street address out of whichever result shape actually carries
    the RIGHT business's address, not just any address.

    `brand_words` is the same vendor-yaml-derived word set `locate_nearest`
    filters Maps cards with, for the identical reason: a query for "LEO GAS
    AND FOOD LLC" surfaces several unrelated "Leo's" businesses (a
    restaurant, a seafood place) ahead of or alongside the real gas station
    in both `local_results` and `organic_results`, so every candidate's own
    title is checked against the destination's brand words before its
    address is trusted -- requiring ALL of them, not just "Leo's".

    A knowledge-graph card (a well-known chain) carries `address`/`title`
    directly and is trusted without the brand check, since the knowledge
    graph is keyed to the query's single best match, not a list of
    candidates to pick wrong from. `local_results` (`local_pack`) and
    `organic_results` are both lists, checked in that order since a
    local-pack result is a map listing while an organic result's address
    (when present at all) is found by regex inside a title/snippet string.
    """
    knowledge_graph = data.get("knowledge_graph") or {}
    if knowledge_graph.get("address"):
        return knowledge_graph["address"], knowledge_graph.get("title", "")

    local_results = data.get("local_results") or []
    places = local_results.get("places", []) if isinstance(local_results, dict) else local_results
    for place in places:
        if not isinstance(place, dict) or not place.get("address"):
            continue
        title = place.get("title", "")
        words = {word.lower() for word in re.findall(r"[A-Za-z]{3,}", title)}
        if brand_words and not brand_words <= words:
            continue
        return place["address"], title

    for result in data.get("organic_results") or []:
        title = result.get("title", "")
        words = {word.lower() for word in re.findall(r"[A-Za-z]{3,}", title)}
        if brand_words and not brand_words <= words:
            continue
        match = _STREET_ADDRESS.search(title) or _STREET_ADDRESS.search(result.get("snippet", ""))
        if match:
            return match.group(0), title

    return "", ""


def search_address(run, request: DistanceRequest) -> SearchAddressResult:
    """Ask SerpAPI's Google Search for the destination's own street address,
    as the last fallback after both the directions box and a Maps search
    near the origin failed to find a plausible match -- the destination may
    be a real business whose listing simply does not carry the brand words
    `locate_nearest` filters on (confirmed live: "LEO GAS AND FOOD LLC" is
    signed and listed on Maps only as "bp", so no amount of word-matching
    against its own name succeeds there).

    Uses SerpAPI rather than scripting `google.com/search` directly -- that
    page returns a CAPTCHA to this box's scripted traffic every time, see
    the module docstring above. SerpAPI is a paid proxy that returns the
    same search a person would see, as structured JSON, with no browser and
    no CAPTCHA involved.
    """
    if request.dry_run:
        message = f"Dry run -- would search for {request.destination!r} via SerpAPI"
        run.console.note(message)
        return SearchAddressResult(message=message)

    known_address = _KNOWN_ADDRESSES.get(_vendor_key(request.destination) or "")
    if known_address:
        message = f"Known address for {request.destination!r}: {known_address}."
        run.console.note(message)
        return SearchAddressResult(found=True, name=request.destination,
                                    address=known_address, message=message)

    api_key = os.environ.get(SERP_API_KEY_ENV, "").strip()
    if not api_key:
        message = f"{SERP_API_KEY_ENV} is not set -- cannot search for an address."
        run.console.note(message)
        return SearchAddressResult(message=message)

    origin = request.origin or HOME_ADDRESS
    query = f"{request.destination} {_origin_locality(origin)}"
    params = {"engine": "google", "q": query,
              "location": SERP_API_LOCATION, "api_key": api_key}
    url = f"{SERP_API_URL}?{urllib.parse.urlencode(params)}"
    data = None
    last_error: Exception | None = None
    for attempt in range(SERP_API_ATTEMPTS):
        try:
            with urllib.request.urlopen(url, timeout=SERP_API_TIMEOUT) as response:  # noqa: S310
                data = json.load(response)
            break
        except Exception as error:  # noqa: BLE001 - a failed search must not crash the run
            last_error = error
    if data is None:
        message = f"SerpAPI search failed: {last_error}"
        run.console.note(message)
        return SearchAddressResult(message=message)

    address, name = _address_from_serpapi(data, _brand_words(request.destination))
    if not address:
        message = f"SerpAPI found no address for {request.destination!r}."
        run.console.note(message)
        return SearchAddressResult(message=message)

    message = f"SerpAPI found {name or request.destination!r} at {address}."
    run.console.note(message)
    return SearchAddressResult(found=True, name=name, address=address, message=message)


__all__ = ["HOME_ADDRESS", "MIN_DESTINATION_LETTERS", "address_from_map_link",
          "build_map_link", "calculate", "locate_nearest", "search_address",
          "validate_destination"]
