#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "selenium"]
# ///
"""ADW Interface Search Open -- search the web headlessly, find the result link
that is the site itself, and open THAT link in the Windows browser, phase by
phase, written in the interface style.

The sibling of `adw_interface_browser_open.py`, and the step past it. That one
hands a link -- or a search URL -- to the Windows launcher ladder and stops; you
land on a results page and still have to pick the right row yourself. This one
does the picking first: it drives a headless browser over a results page,
reads the organic result links, chooses the one that is the site you asked for,
and only then spawns the real browser. The point is a logged-in session: you
asked for "Fifth Third Bank", you get `https://www.53.com/` in a real window,
and you log in by hand from there.

So unlike its sibling, this ADW *does* drive a page -- that is the whole added
value, and it is why `browser_manager` exists. The headless driver is
`WebBrowserManager` from `/home/adamsl/selenium-headless/browser_manager/`,
reused as-is rather than reimplemented: it already owns the headless Chrome
options, the navigate/find/quit lifecycle, and the CSS-selector lookup this
needs. `SeleniumResultPageReader` below is the only class allowed to import
it -- that is the DIP boundary, the same way `adapter.py` is the only file in
`claude_agent_adapter/` allowed to import `claude_agent_sdk`.

Everything after the link is chosen is `adw_modules/map_open.py`, untouched:
`validate()` scheme-checks the chosen link, `select_launcher()` walks the
launcher ladder, and `launch()` spawns it detached. A second copy of any of
those three would be the defect, not the reuse -- and both of that module's
invariants carry over because they live there: never block (the browser is a
detached spawn whose exit status is never read) and never raise (a blocked
results page or a missing launcher is a message, not a traceback).

WHICH SEARCH ENGINE, AND WHY NOT GOOGLE FIRST

`--phrase "Fifth Third Bank"` and
`--search-url "https://www.google.com/search?q=Fifth+Third+Bank"` are the same
request here: the URL form is read for its `q=` parameter and the query is run
against the backend ladder, rather than being fetched as given. That is a
deliberate substitution and the operator's line says so when it happens,
because handing back a different engine's answer silently would be a lie about
where the link came from.

The ladder is Bing, then DuckDuckGo, then Google, and that order is measured on
this box rather than preferred:

* Google answers headless Chrome with `/sorry/index` -- a bot challenge, three
  anchors, no results.
* DuckDuckGo's html endpoint answers with "Unfortunately, bots use DuckDuckGo
  too" and a CAPTCHA.
* Bing answers with a full results page, ten organic rows.

Both blocked engines stay on the ladder: a challenge page yields zero
candidates and costs one page load, the next backend is tried, and the day
either one stops challenging us it starts working again with no edit here.

If every backend comes up empty, the run does not fabricate a destination. It
falls back to opening the SEARCH page itself in the real browser -- which is
exactly what the sibling ADW would have done -- and says that is what happened,
so you can click the row yourself.

Six phases:

* `request` (engineer) -- records the phrase or search URL and the options.
* `search_query` (code) -- resolves the query text, reading `q=` out of a
  search URL when one was given instead of a phrase.
* `find_link` (code) -- walks the backend ladder with the headless browser,
  collects organic result links, and chooses one. The only phase that loads a
  page, and the only one that costs network time.
* `validate` (code) -- `map_open.validate()` on the chosen link, before any
  process is spawned. A link a search engine handed us earns no more trust
  than a typed one, so it gets the identical check.
* `select_launcher` (code) -- the first launcher on `map_open.LAUNCHERS`
  present on this box. Spawns nothing.
* `launch` (code) -- hands the link over detached via `map_open.launch()`,
  then writes the operator's one line itself, because `map_open`'s own wording
  talks about "this expense's map".

No agent phase: choosing the first organic result that is not an encyclopedia
or a social profile needs no judgement, so `REQUIRED_AGENTS` is empty and every
phase is `kind="code"`. `--prefer-domain` is the steering wheel for the rare
case where it needs one.

Usage:
    uv run adws/adw_interface_search_open.py "<why this is being opened>" \\
        --phrase "Fifth Third Bank" \\
        [--search-url "https://www.google.com/search?q=Fifth+Third+Bank"] \\
        [--prefer-domain 53.com] [--skip-domain wikipedia.org] \\
        [--engine bing|duckduckgo|google] [--keep-path] [--dry-run] \\
        [--message-out /tmp/line.txt] \\
        [--config adws/adw_sssf_config/sssf.config.yaml] [--adw-id a1b2c3d4]

Phases: engineer(request) -> code(search_query) -> code(find_link)
        -> code(validate) -> code(select_launcher) -> code(launch)
"""

from __future__ import annotations

import argparse
import base64
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

from pydantic import BaseModel, Field

from sssf_roles import ( ConsoleLifecycleLog, IApplication, IArgumentParser, IConfigLoader,
                         ILifecycleLog, IPhase, IPhaseExecutor, IPhaseHandle, ISessionFactory,
                         LifecycleLogged, ManualContextPhaseExecutor, PrintLifecycleLog,
                         SessionRunFactory, SssfConfigLoader )

from adw_modules import map_open, utils
from adw_modules.data_types import LaunchResult, LauncherChoice, LinkCheck, MapOpenRequest, PhaseParams
from adw_modules.runner import Run

REQUIRED_AGENTS: list[ str ] = []   # every phase here is code; no judgement is needed

# Where `browser_manager` lives. A path rather than an install because
# selenium-headless is a separate repository with its own history and no
# packaging -- see `/home/adamsl/selenium-headless/CLAUDE.md`. Only
# SeleniumResultPageReader uses it, and only inside a method.
SELENIUM_HEADLESS_ROOT = "/home/adamsl/selenium-headless"

# How long `find_elements` waits for a selector before reporting none. A
# blocked backend genuinely has no results, so this is the price of learning
# that -- paid once per backend, which is why the ladder is short.
PAGE_WAIT_SECONDS = 5

# Hosts that answer a brand-name search well but are never the thing you meant
# when you said "open the bank so I can log in". Suffix-matched against the
# candidate's host, so "en.wikipedia.org" matches "wikipedia.org".
AGGREGATOR_HOSTS: tuple[ str, ... ] = (
    "wikipedia.org", "wikiwand.com", "facebook.com", "instagram.com", "x.com",
    "twitter.com", "linkedin.com", "youtube.com", "reddit.com", "yelp.com",
    "tripadvisor.com", "glassdoor.com", "indeed.com", "crunchbase.com",
    "bbb.org", "trustpilot.com", "mapquest.com", "apple.com/maps",
)


# -- Values (not behavior) ---------------------------------------------------

@dataclass( frozen=True )
class SearchOpenRequest:
    """Parsed command-line intent. Mirrors sssf_roles.PromptRequest's shape."""
    prompt:         str
    phrase:         str
    search_url:     str
    prefer_domains: tuple[ str, ... ]
    skip_domains:   tuple[ str, ... ]
    engine:         str
    keep_path:      bool
    dry_run:        bool
    message_out:    str
    config:         str
    adw_id:         str | None


class SearchQuery( BaseModel ):
    """Phase 2's verdict: what we are going to search for, and where it came from.

    `substituted` is kept separate from `text` because it changes the operator's
    sentence: a query read out of a Google URL is being run against a different
    engine than the one in that URL, and that is a fact the operator has to be
    told rather than one we quietly absorb.
    """

    text:        str = ""
    usable:      bool = False
    reason:      str = ""
    substituted: bool = False   # the query was read out of a search URL's q=


class LinkCandidate( BaseModel ):
    """One organic result link, as read off a results page."""

    url:     str
    host:    str = ""
    title:   str = ""
    backend: str = ""


class LinkPick( BaseModel ):
    """Phase 3's result: which candidate won, out of what, from which backend.

    `probed` and `candidates` are kept even on success, for the same reason
    `LauncherChoice.probed` is: a pick that came from the third backend after
    two challenge pages reads very differently from one Bing answered first.
    """

    url:        str = ""
    home_page:  str = ""        # the chosen link trimmed to scheme + host
    found:      bool = False
    backend:    str = ""
    reason:     str = ""
    probed:     list[ str ] = Field( default_factory=list )
    candidates: list[ LinkCandidate ] = Field( default_factory=list )

    @property
    def target( self ) -> str:
        """The link this pick wants opened -- the trimmed form when there is one."""
        return self.home_page or self.url


# -- Interfaces --------------------------------------------------------------

@runtime_checkable
class ISearchBackend( Protocol ):
    """One search engine: its query URL, its result anchors, and its redirects.

    Three questions, because engines differ on exactly three things and nothing
    else. Keeping them one object per engine is what makes the ladder a list
    rather than an if/elif -- a fourth engine is one more entry in BACKENDS.
    """

    @property
    def name( self ) -> str: ...

    @property
    def selector( self ) -> str:
        """CSS selector matching the organic results' anchors, and not the chrome."""

    def url_for( self, query: str ) -> str:
        """This engine's results URL for `query`."""

    def destination( self, href: str ) -> str:
        """The real destination behind `href`, unwrapping any click-tracking."""


@runtime_checkable
class IResultPageReader( Protocol ):
    """Loads a results page and reads its result links. The browser lives here."""

    def read( self, url: str, backend: ISearchBackend ) -> list[ LinkCandidate ]: ...

    def close( self ) -> None:
        """Release the browser, if one was ever started."""


@runtime_checkable
class ILinkChooser( Protocol ):
    """Picks the one candidate to open, or reports that none qualified."""

    def choose( self, candidates: list[ LinkCandidate ], probed: list[ str ] ) -> LinkPick: ...


# -- Search backends --------------------------------------------------------

@dataclass( frozen=True )
class QueryUrlBackend:
    """A backend whose results URL is a template and whose links are plain hrefs.

    Google is this shape: the anchor's href already IS the destination, so
    `destination()` is the identity. Bing and DuckDuckGo subclass the idea by
    composition below, each with its own unwrapping.
    """

    name_:     str
    template:  str          # "...?q={q}", with the query percent-encoded in
    selector_: str

    @property
    def name( self ) -> str:
        return self.name_

    @property
    def selector( self ) -> str:
        return self.selector_

    def url_for( self, query: str ) -> str:
        return self.template.format( q=quote_plus( query ))

    def destination( self, href: str ) -> str:
        return href


@dataclass( frozen=True )
class BingBackend( QueryUrlBackend ):
    """Bing, whose result anchors point at `bing.com/ck/a?...&u=a1<base64>`.

    The destination is base64url inside the `u` parameter behind an "a1"
    version tag, which is why the anchor's own href is useless as a target and
    its `.text` is empty in a headless render. Measured on this box as the one
    backend that answers a headless browser with real results -- see the module
    docstring.
    """

    def destination( self, href: str ) -> str:
        wrapped = ( parse_qs( urlparse( href ).query ).get( "u" ) or [ "" ])[ 0 ]
        if not wrapped.startswith( "a1" ):
            return href if "bing.com/ck/" not in href else ""
        payload = wrapped[ 2: ]
        payload += "=" * ( -len( payload ) % 4 )    # base64url, stripped of padding
        try:
            return base64.urlsafe_b64decode( payload ).decode( "utf-8", "replace" )
        except ( ValueError, TypeError ):
            # A redirect we cannot read is not a destination. Dropping it is
            # right: the next candidate is one row down, and guessing here
            # would hand a browser a string we never decoded.
            return ""


@dataclass( frozen=True )
class DuckDuckGoBackend( QueryUrlBackend ):
    """DuckDuckGo's html endpoint, whose links are `/l/?uddg=<percent-encoded>`."""

    def destination( self, href: str ) -> str:
        wrapped = ( parse_qs( urlparse( href ).query ).get( "uddg" ) or [ "" ])[ 0 ]
        return unquote( wrapped ) if wrapped else href


#: Tried in order; the first backend that yields a usable candidate wins.
#: Bing leads because it is the only one of the three that currently answers a
#: headless browser at all. The other two stay on the ladder deliberately -- a
#: challenge page is zero candidates and one page load, not an error, and if
#: either stops challenging us it resumes working with no edit here.
BACKENDS: tuple[ ISearchBackend, ... ] = (
    BingBackend( name_="bing",
                 template="https://www.bing.com/search?q={q}",
                 selector_="li.b_algo h2 a" ),
    DuckDuckGoBackend( name_="duckduckgo",
                       template="https://html.duckduckgo.com/html/?q={q}",
                       selector_="a.result__a" ),
    QueryUrlBackend( name_="google",
                     template="https://www.google.com/search?q={q}",
                     selector_="div#search a[href^='http']" ),
)


# -- Adapters ---------------------------------------------------------------

class SeleniumResultPageReader:
    """IResultPageReader over selenium-headless's `WebBrowserManager`.

    The only class here allowed to know a browser is involved, and the only one
    that imports anything from `/home/adamsl/selenium-headless`. The import is
    inside `_browser()` rather than at module scope on purpose: `--help`, a bad
    phrase, and an unreachable config should all fail without paying for Chrome
    and a webdriver, and the same lazy-heavy-import rule already governs
    `agent_memory/adapters/embedding_profiles.py`.

    One browser is started on the first read and reused across the ladder, so
    three backends cost one Chrome rather than three.
    """

    def __init__( self, wait_seconds: int = PAGE_WAIT_SECONDS,
                  headless_root: str = SELENIUM_HEADLESS_ROOT ):
        self._wait_seconds  = wait_seconds
        self._headless_root = headless_root
        self._browser: object | None = None

    def _browser_or_start( self ) -> object:
        if self._browser is None:
            if self._headless_root not in sys.path:
                sys.path.insert( 0, self._headless_root )
            from browser_manager import WebBrowserManager   # noqa: PLC0415  (see docstring)

            browser = WebBrowserManager()
            # A blocked backend has no results, and `find_elements` must be
            # allowed to wait before saying so -- otherwise a slow render is
            # misread as a challenge page. WebBrowserManager owns the driver
            # and exposes no wait setting, so the one knob is set here.
            browser.driver.implicitly_wait( self._wait_seconds )
            self._browser = browser
        return self._browser

    def read( self, url: str, backend: ISearchBackend ) -> list[ LinkCandidate ]:
        """Load `url` and return its result links. Never raises -- see module docstring.

        A backend that times out, challenges us, or renames its result class
        all arrive here as the same thing: an empty list. That is the signal the
        ladder is built on, so it must not be an exception.
        """
        browser = self._browser_or_start()
        try:
            browser.navigate_to_page( url )
            anchors = browser.find_elements( backend.selector )
        except Exception:
            return []

        candidates: list[ LinkCandidate ] = []
        for anchor in anchors:
            try:
                href  = anchor.get_attribute( "href" ) or ""
                title = ( anchor.text or "" ).strip()
            except Exception:
                continue        # a stale element is one row lost, not a failed read
            destination = backend.destination( href )
            if not destination:
                continue
            parsed = urlparse( destination )
            if parsed.scheme not in ( "http", "https" ) or not parsed.netloc:
                continue
            candidates.append( LinkCandidate( url=destination, host=parsed.netloc.lower(),
                                              title=title, backend=backend.name ))
        return candidates

    def close( self ) -> None:
        if self._browser is None:
            return
        try:
            self._browser.quit()
        except Exception:
            pass                # a browser we cannot close is not a failed run
        finally:
            self._browser = None


class FirstOrganicLinkChooser:
    """ILinkChooser: the first organic result that is plausibly the site itself.

    Deliberately simple, in this order:

    1. A `--prefer-domain` match wins outright, whatever its rank. That is the
       steering wheel for the case where rank one is wrong.
    2. Otherwise the first candidate whose host is not a search engine and not
       in AGGREGATOR_HOSTS. Rank is the engine's own judgement about which
       result is the site, and it is better at that than a heuristic here --
       token-matching the query against the host would have ranked
       "Fifth Third Bank" nowhere near `53.com`.
    3. Nothing qualified -> `found=False`, and the ADW falls back to opening the
       search page rather than inventing a destination.

    `home_page` trims the winner to scheme + host because the stated purpose is
    logging in by hand: `https://www.53.com/content/fifth-third/en.html` and
    `https://www.53.com/` are the same front door, and the short one is the one
    that survives a content reshuffle. `--keep-path` turns the trimming off.
    """

    def __init__( self, prefer_domains: tuple[ str, ... ] = (),
                  skip_domains: tuple[ str, ... ] = (),
                  trim_to_home_page: bool = True ):
        self._prefer = tuple( domain.lower().lstrip( "." ) for domain in prefer_domains if domain )
        self._skip   = tuple( domain.lower().lstrip( "." )
                              for domain in ( *AGGREGATOR_HOSTS, *skip_domains ) if domain )
        self._trim   = trim_to_home_page

    def choose( self, candidates: list[ LinkCandidate ], probed: list[ str ] ) -> LinkPick:
        if not candidates:
            return LinkPick( probed=probed, candidates=candidates,
                             reason="no search backend returned a usable result link" )

        preferred = [ c for c in candidates if self._matches( c, self._prefer ) ]
        if preferred:
            return self._pick( preferred[ 0 ], candidates, probed,
                               f"matches the preferred domain { preferred[ 0 ].host }" )

        for candidate in candidates:
            if self._matches( candidate, self._skip ):
                continue
            return self._pick( candidate, candidates, probed,
                               f"first organic result on { candidate.backend }" )

        return LinkPick( probed=probed, candidates=candidates,
                         reason=( "every result was an encyclopedia, directory or social "
                                  "profile rather than the site itself" ))

    def _pick( self, candidate: LinkCandidate, candidates: list[ LinkCandidate ],
               probed: list[ str ], reason: str ) -> LinkPick:
        parsed = urlparse( candidate.url )
        return LinkPick( url=candidate.url,
                         home_page=f"{ parsed.scheme }://{ parsed.netloc }/" if self._trim else "",
                         found=True, backend=candidate.backend, reason=reason,
                         probed=probed, candidates=candidates )

    @staticmethod
    def _matches( candidate: LinkCandidate, domains: tuple[ str, ... ] ) -> bool:
        host = candidate.host
        return any( host == domain or host.endswith( f".{ domain }" )
                    or f"{ host }{ urlparse( candidate.url ).path }".startswith( domain )
                    for domain in domains )


# -- Phases ------------------------------------------------------------------

class RequestCapturePhase( LifecycleLogged, IPhase ):
    """Records what is being searched for and under what options, nothing more."""

    def __init__( self, engineer: str, request: SearchOpenRequest, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._engineer = engineer
        self._request  = request

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="request", kind="engineer", owner=self._engineer,
                            description="Record which search is being run and which result link is wanted" )

    def act( self, handle: IPhaseHandle ) -> None:
        r = self._request
        handle.log( input=r.prompt, phrase=r.phrase or "(none)",
                    search_url=r.search_url or "(none)", engine=r.engine or "(ladder)",
                    prefer=", ".join( r.prefer_domains ) or "(none)",
                    keep_path=r.keep_path, dry_run=r.dry_run )


class SearchQueryPhase( LifecycleLogged, IPhase ):
    """Resolves the query text, reading `q=` out of a search URL when given one.

    Not folded into argument parsing: which of the two inputs won, and whether
    a Google URL was substituted for a Bing search, is a decision the operator
    reads in the trace and in the final sentence. A decision with a consequence
    that visible gets its own phase.
    """

    #: The parameter every engine on the ladder calls its query. `p` is Yahoo's
    #: and costs nothing to accept, since a URL carrying one of these is
    #: unambiguously a search URL.
    QUERY_PARAMS = ( "q", "query", "p" )

    def __init__( self, request: SearchOpenRequest, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._request = request

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="search_query", kind="code", owner="browser",
                            description="Settle on the text to search for, reading it out of a search URL if that is what was given" )

    def act( self, handle: IPhaseHandle ) -> SearchQuery:
        query = self._resolve()
        handle.log( query=query.text or "(none)", usable=query.usable,
                    substituted=query.substituted, reason=query.reason )
        return query

    def _resolve( self ) -> SearchQuery:
        phrase = " ".join( self._request.phrase.split())
        if phrase:
            return SearchQuery( text=phrase, usable=True,
                                reason=f"searching for { phrase!r } as given" )

        raw = self._request.search_url.strip()
        if not raw:
            return SearchQuery( reason="neither a search phrase nor a search URL was given" )

        params = parse_qs( urlparse( raw ).query )
        for name in self.QUERY_PARAMS:
            found = " ".join(( params.get( name ) or [ "" ])[ 0 ].split())
            if found:
                return SearchQuery( text=found, usable=True, substituted=True,
                                    reason=( f"read { found!r } out of the given search URL's "
                                             f"{ name }= parameter" ))
        return SearchQuery( reason=f"no search terms in { raw!r } -- it carries no q= parameter" )


class FindLinkPhase( LifecycleLogged, IPhase ):
    """Walks the backend ladder with the headless browser and chooses one link.

    The only phase that loads a page. It owns the reader's lifetime -- the
    browser is closed here whatever happens, because a phase that leaks a
    Chrome is a phase that leaks one per run.
    """

    def __init__( self, query: SearchQuery, backends: tuple[ ISearchBackend, ... ],
                  reader: IResultPageReader, chooser: ILinkChooser, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._query    = query
        self._backends = backends
        self._reader   = reader
        self._chooser  = chooser

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="find_link", kind="code", owner="browser",
                            description="Read the organic result links off a headless results page and choose the one that is the site itself" )

    def act( self, handle: IPhaseHandle ) -> LinkPick:
        probed: list[ str ] = []
        pick = LinkPick( probed=probed, reason="no search backend was available to try" )
        try:
            for backend in self._backends:
                probed.append( backend.name )
                candidates = self._reader.read( backend.url_for( self._query.text ), backend )
                handle.log( backend=backend.name, candidates=len( candidates ))
                pick = self._chooser.choose( candidates, list( probed ))
                if pick.found:
                    break
        finally:
            self._reader.close()

        handle.log( found=pick.found, chosen=pick.target or "(none)",
                    backend=pick.backend or "(none)", reason=pick.reason,
                    probed=", ".join( probed ))
        return pick


class ValidateLinkPhase( LifecycleLogged, IPhase ):
    """Scheme-checks the chosen link before any process is spawned.

    `map_open.validate()` as-is, with `source="place-search"` -- the closest of
    its three source literals to what happened, since the link came off a
    search and not out of a column. A link a search engine handed us is exactly
    as unverified as a typed one, so it gets the identical check.
    """

    def __init__( self, run: Run, map_request: MapOpenRequest, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._run         = run
        self._map_request = map_request

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="validate", kind="code", owner="browser",
                            description="Decide whether the chosen result link is safe to hand a browser, before any process is spawned" )

    def act( self, handle: IPhaseHandle ) -> LinkCheck:
        check = map_open.validate( self._run, self._map_request, source="place-search" )
        handle.log( openable=check.openable, target=check.target or "(none)",
                    reason=check.reason )
        return check


class SelectLauncherPhase( LifecycleLogged, IPhase ):
    """Walks `map_open.LAUNCHERS` for the first one present on this box."""

    def __init__( self, run: Run, map_request: MapOpenRequest, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._run         = run
        self._map_request = map_request

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="select_launcher", kind="code", owner="browser",
                            description="Find the first launcher on the ladder that exists on this box, spawning nothing" )

    def act( self, handle: IPhaseHandle ) -> LauncherChoice:
        choice = map_open.select_launcher( self._run, self._map_request )
        handle.log( found=choice.found, launcher=choice.name or "(none)",
                    probed=", ".join( choice.probed ))
        return choice


class LaunchPhase( LifecycleLogged, IPhase ):
    """Hands the link to the chosen launcher, detached, and writes the
    operator's one line itself.

    `map_open.launch()` does the spawn -- the never-block, never-raise
    behaviour lives there and nowhere else -- but its `message` talks about
    "this expense's map", and this one has to say something that module has no
    words for: which engine answered, whether a search URL was substituted, and
    whether what opened is the site or merely the results page.
    """

    def __init__( self, run: Run, map_request: MapOpenRequest, check: LinkCheck,
                  choice: LauncherChoice, query: SearchQuery, pick: LinkPick,
                  logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._run         = run
        self._map_request = map_request
        self._check       = check
        self._choice      = choice
        self._query       = query
        self._pick        = pick

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="launch", kind="code", owner="browser",
                            description="Hand the chosen link to the launcher detached, and produce the operator's one line" )

    def act( self, handle: IPhaseHandle ) -> tuple[ LaunchResult, str ]:
        result  = map_open.launch( self._run, self._map_request, self._check, self._choice )
        message = self._operator_message( result )
        handle.log( launched=result.launched, launcher=result.launcher or "(none)",
                    message=message )
        return result, message

    def _operator_message( self, result: LaunchResult ) -> str:
        check, choice, pick = self._check, self._choice, self._pick
        if not check.openable:
            return ( f"Could not open anything: no usable link came out of searching for "
                     f"{ self._query.text!r } ({ pick.reason })." )
        if not choice.found:
            return ( f"No Windows browser launcher found (tried { ', '.join( choice.probed )}). "
                     f"Open manually: { check.target }" )
        if not result.launched:
            return result.message   # "Could not start X: <error>" -- already generic
        return f"{ self._what_opened() } You can log in manually from there."

    def _what_opened( self ) -> str:
        """The first half of the success line: what opened, and where it came from."""
        check, choice, pick = self._check, self._choice, self._pick
        via = f"via { choice.name }"
        if not pick.found:
            # The fallback. Saying "opened the bank" here would be the one
            # outright lie this ADW can tell, so it says what it really did.
            return ( f"I could not pick a result link ({ pick.reason }), so I opened the "
                     f"search page itself, { check.target }, { via }." )
        asked = ( f"searched { self._pick.backend } for { self._query.text!r }"
                  if self._query.substituted
                  else f"searched for { self._query.text!r }" )
        return f"I { asked } and opened { check.target } { via }."


# -- Argument parsing --------------------------------------------------------

class ArgparseSearchArgumentParser( LifecycleLogged, IArgumentParser ):
    """The only class allowed to know this CLI is built on argparse."""

    def __init__( self, description: str, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._description = description

    def parse( self, argv: list[ str ]) -> SearchOpenRequest:
        parser = argparse.ArgumentParser( description=self._description )
        parser.add_argument( "prompt", help="inline text or a path to a prompt file; why this is being opened" )
        parser.add_argument( "--phrase", default="",
                             help="what to search for, e.g. \"Fifth Third Bank\"" )
        parser.add_argument( "--search-url", default="",
                             help="a search URL to take the query from, e.g. a google.com/search?q=... link" )
        parser.add_argument( "--prefer-domain", action="append", default=[],
                             help="a domain that wins outright if it appears in the results; repeatable" )
        parser.add_argument( "--skip-domain", action="append", default=[],
                             help="a domain to pass over, on top of the built-in aggregator list; repeatable" )
        parser.add_argument( "--engine", default="", choices=[ "", *( b.name for b in BACKENDS ) ],
                             help="pin one search backend instead of walking the ladder" )
        parser.add_argument( "--keep-path", action="store_true",
                             help="open the result link exactly as found, instead of trimming it to the site's front page" )
        parser.add_argument( "--dry-run", action="store_true",
                             help="search and resolve a launcher, but spawn no browser" )
        parser.add_argument( "--message-out", default="",
                             help="write the operator's one line here, for a non-terminal caller" )
        parser.add_argument( "--config", default="adws/adw_sssf_config/sssf.config.yaml" )
        parser.add_argument( "--adw-id", default=None, help="join or pin an existing session" )
        parsed = parser.parse_args( argv )
        return SearchOpenRequest( prompt=utils.resolve_prompt( parsed.prompt ),
                                  phrase=parsed.phrase, search_url=parsed.search_url,
                                  prefer_domains=tuple( parsed.prefer_domain ),
                                  skip_domains=tuple( parsed.skip_domain ),
                                  engine=parsed.engine, keep_path=parsed.keep_path,
                                  dry_run=parsed.dry_run, message_out=parsed.message_out,
                                  config=parsed.config, adw_id=parsed.adw_id )


# -- Composition root's one orchestrator -------------------------------------

class SearchOpenApplication( LifecycleLogged, IApplication ):
    """Orchestrates the six-phase run. Depends only on interfaces, plus the
    concrete `Run` every phase already shares -- the same exception the shipped
    template itself makes (`IPhaseExecutor.execute(run: Run, ...)`).

    The reader and chooser arrive as factories rather than instances because
    both are per-request: the chooser is built from this run's
    `--prefer-domain`/`--keep-path`, and the reader owns a Chrome that must not
    exist before `--help` has had its say.
    """

    def __init__( self,
                  argument_parser: IArgumentParser,
                  config_loader:   IConfigLoader,
                  session_factory: ISessionFactory,
                  phase_executor:  IPhaseExecutor,
                  reader_factory:  object,
                  chooser_factory: object,
                  backends:        tuple[ ISearchBackend, ... ],
                  logger:          ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._argument_parser = argument_parser
        self._config_loader   = config_loader
        self._session_factory = session_factory
        self._phase_executor  = phase_executor
        self._reader_factory  = reader_factory
        self._chooser_factory = chooser_factory
        self._backends        = backends

    def run( self, argv: list[ str ]) -> int:
        request = self._argument_parser.parse( argv )
        cfg     = self._config_loader.load( request.config )
        self._config_loader.validate( cfg, REQUIRED_AGENTS )
        run = self._session_factory.create( cfg, request.adw_id )

        session_log: ILifecycleLog = ConsoleLifecycleLog( run )

        self._phase_executor.execute( run, RequestCapturePhase( run.engineer, request, session_log ))
        query = self._phase_executor.execute( run, SearchQueryPhase( request, session_log ))

        if not query.usable:
            # Nothing to search for is a finished run, not a crash: the sentence
            # says what was missing and the RUN records that nothing opened.
            message = ( f"I didn't catch what to look up ({ query.reason }). Give me "
                        f"something to search for and I'll find the site and open it." )
            self._report( request, message )
            return run.finish( accepted=False, reason=message )

        pick = self._phase_executor.execute(
            run, FindLinkPhase( query, self._ladder( request ),
                                self._reader_factory(), self._chooser_factory( request ),
                                session_log ))

        # The fallback: no pick means we open the results page itself rather
        # than inventing a destination. It is the sibling ADW's behaviour,
        # reached deliberately and reported as such -- see LaunchPhase.
        target = pick.target or self._ladder( request )[ 0 ].url_for( query.text )

        base  = MapOpenRequest( url=target, dry_run=request.dry_run )
        check = self._phase_executor.execute( run, ValidateLinkPhase( run, base, session_log ))
        choice = self._phase_executor.execute( run, SelectLauncherPhase( run, base, session_log ))
        result, message = self._phase_executor.execute(
            run, LaunchPhase( run, base, check, choice, query, pick, session_log ))

        self._report( request, message )

        # A dry run resolved everything a real one would and deliberately
        # spawned nothing, so it is accepted on that basis -- not on
        # `launched`, which it honestly reports as false.
        accepted = result.launched or ( request.dry_run and check.openable and choice.found )
        return run.finish( accepted=accepted, reason=message )

    def _ladder( self, request: SearchOpenRequest ) -> tuple[ ISearchBackend, ... ]:
        """This run's backends -- the whole ladder, or the one `--engine` pinned."""
        if not request.engine:
            return self._backends
        return tuple( b for b in self._backends if b.name == request.engine )

    def _report( self, request: SearchOpenRequest, message: str ) -> None:
        """Write the operator's line where a non-terminal caller can read it.

        The caller may be a receptionist rather than a terminal, and it reads
        this file -- same contract as adw_interface_browser_open.py.
        """
        if request.message_out:
            Path( request.message_out ).write_text( message + "\n" )


def build_application() -> IApplication:
    """Wire the concrete adapters into the application. The one spot that may."""
    bootstrap_log: ILifecycleLog = PrintLifecycleLog()
    return SearchOpenApplication(
        argument_parser = ArgparseSearchArgumentParser( __doc__, bootstrap_log ),
        config_loader   = SssfConfigLoader(              bootstrap_log          ),
        session_factory = SessionRunFactory(             bootstrap_log          ),
        phase_executor  = ManualContextPhaseExecutor(    bootstrap_log          ),
        reader_factory  = SeleniumResultPageReader,
        chooser_factory = lambda request: FirstOrganicLinkChooser(
            prefer_domains    = request.prefer_domains,
            skip_domains      = request.skip_domains,
            trim_to_home_page = not request.keep_path, ),
        backends        = BACKENDS,
        logger          =                                bootstrap_log,          )


if __name__ == "__main__":
    sys.exit( build_application().run( sys.argv[ 1: ]))
