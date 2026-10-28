"""browser_session's decisions, with fakes for every interface: no Chrome,
no network, no real waiting.

Worth pinning are the decisions that can be wrong without crashing: which
tab, which element (a hidden duplicate must not win), that --dry-run really
does not click, that a retry stops once it works, that the attacher never
closes the browser, and that probes say "nothing listening" instead of raising.
"""
import sys
from pathlib import Path

import pytest

ADWS_DIR = Path( __file__ ).resolve().parents[ 2 ]
if str( ADWS_DIR ) not in sys.path:
    sys.path.insert( 0, str( ADWS_DIR ))

from browser_session.chrome_command import ChromeCommandBuilder               # noqa: E402
from browser_session.chrome_launch import DebugChromeLauncher                 # noqa: E402
from browser_session.clickers import DryRunClicker, ElementClicker, RetryingClickAction  # noqa: E402
from browser_session.detach_policies import StopDriverServiceDetach           # noqa: E402
from browser_session.element_specs import clickable                           # noqa: E402
from browser_session.endpoint_probe import DevToolsEndpointProbe              # noqa: E402
from browser_session.locator_registry import LocatorRegistry, default_registry  # noqa: E402
from browser_session.locators import BUTTON_LIKE, CssLocator                  # noqa: E402
from browser_session.models import ClickResult, DebugEndpoint, LocatorSpec, TabInfo  # noqa: E402
from browser_session.parts_factory import DefaultClickPartsFactory            # noqa: E402
from browser_session.readiness import PollingReadinessWaiter                  # noqa: E402
from browser_session.session_attacher import ManagerSessionAttacher           # noqa: E402
from browser_session.site_targets import ALL_TARGETS, FIFTH_THIRD_LOGIN       # noqa: E402
from browser_session.tab_selector import SpecificationTabSelector             # noqa: E402
from browser_session.target_catalog import InMemoryTargetCatalog              # noqa: E402
from browser_session.text_matchers import ContainsTextMatcher, ExactTextMatcher  # noqa: E402
from browser_session.url_specs import AllOfUrlSpecification, HostSuffixSpecification, HttpsSpecification  # noqa: E402

FDS = "button[fds-button][type='submit']"


# -- fakes, one per interface ------------------------------------------------

class FakeElement:                                  # IPageElement
    def __init__( self, text="", shown=True, enabled=True, fail_times=0 ):
        self.text, self._shown, self._enabled = text, shown, enabled
        self._fail_times, self.clicks = fail_times, 0

    def is_displayed( self ):
        return self._shown

    def is_enabled( self ):
        return self._enabled

    def click( self ):
        if self._fail_times > 0:
            self._fail_times -= 1
            raise RuntimeError( "element click intercepted" )
        self.clicks += 1


class FakeFinder:                                   # IElementFinder
    def __init__( self, by_selector ):
        self._by = by_selector

    def find( self, css ):
        return list( self._by.get( css, []))


class FakeClock:                                    # IClock
    def __init__( self ):
        self.t, self.slept = 0.0, []

    def now( self ):
        return self.t

    def sleep( self, seconds ):
        self.slept.append( seconds )
        self.t += seconds


class FakeService:                                  # IDriverService
    stopped = False

    def stop( self ):
        self.stopped = True


class FakeSwitch:                                   # ISwitchTo
    def __init__( self, driver ):
        self._driver = driver

    def window( self, handle ):
        self._driver.current_window_handle = handle


class FakeDriver:                                   # IWindowDriver
    def __init__( self, urls ):
        self._urls = urls
        self.window_handles = list( urls )
        self.current_window_handle = self.window_handles[ 0 ]
        self.switch_to = FakeSwitch( self )
        self.service = FakeService()
        self.quit_called = False

    @property
    def current_url( self ):
        return self._urls[ self.current_window_handle ]

    def quit( self ):
        self.quit_called = True


class FakeManager:                                  # IBrowserManager
    def __init__( self, driver, elements ):
        self.driver, self._elements = driver, elements

    def find_elements( self, css ):
        return self._elements.get( css, [])


# -- text matchers and element specifications ---------------------------------

def test_exact_matcher_trims_and_ignores_case_but_not_extra_words():
    m = ExactTextMatcher( "Log In" )
    assert m.matches( "  log   in " ) and not m.matches( "Log In with passkey" )


def test_contains_matcher():
    assert ContainsTextMatcher( "log in" ).matches( "Log In with passkey" )


def test_clickable_needs_shown_and_enabled():
    spec = clickable()
    assert spec.is_satisfied_by( FakeElement())
    assert not spec.is_satisfied_by( FakeElement( shown=False ))
    assert not spec.is_satisfied_by( FakeElement( enabled=False ))


# -- locators -----------------------------------------------------------------

def test_text_locator_skips_a_hidden_duplicate():
    hidden, shown = FakeElement( " Log In ", shown=False ), FakeElement( " Log In " )
    locator = default_registry().create( LocatorSpec( kind="button_text", value="Log In" ))
    assert locator.locate( FakeFinder({ BUTTON_LIKE: [ FakeElement( "Back" ), hidden, shown ]})) is shown


def test_chain_falls_back_to_css_and_records_the_walk():
    fds   = FakeElement( "Sign On" )
    chain = default_registry().chain( FIFTH_THIRD_LOGIN.locators )
    assert chain.locate( FakeFinder({ BUTTON_LIKE: [], FDS: [ fds ]})) is fds
    assert chain.tried == [ "text:exact:log in", f"css:{ FDS }" ]
    assert chain.winner == f"css:{ FDS }"


def test_chain_finds_nothing_when_nothing_is_usable():
    chain = default_registry().chain( FIFTH_THIRD_LOGIN.locators )
    assert chain.locate( FakeFinder({ FDS: [ FakeElement( enabled=False )]})) is None
    assert chain.winner == ""


def test_registry_is_open_for_new_kinds_and_loud_about_unknown_ones():
    registry = LocatorRegistry()
    registry.register( "css", lambda v: CssLocator( v, clickable()))
    assert registry.create( LocatorSpec( kind="css", value="#x" )).label == "css:#x"
    with pytest.raises( KeyError ):
        registry.create( LocatorSpec( kind="button_text", value="Log In" ))


# -- tabs -----------------------------------------------------------------------

def test_tab_selector_wants_https_and_the_host_on_a_dot_boundary():
    spec = AllOfUrlSpecification([ HttpsSpecification(), HostSuffixSpecification( "53.com" )])
    tabs = [ TabInfo( handle="a", url="https://not53.com/" ),
             TabInfo( handle="b", url="http://secure.53.com/olb/login" ),
             TabInfo( handle="c", url="https://secure.53.com/olb/login" )]
    choice = SpecificationTabSelector( spec ).choose( tabs )
    assert choice.found and choice.handle == "c" and len( choice.seen ) == 3
    assert not SpecificationTabSelector( spec ).choose( tabs[ :2 ]).found


# -- clicking -------------------------------------------------------------------

def test_dry_run_clicker_never_clicks():
    element = FakeElement( "Log In" )
    result  = DryRunClicker().perform( element )
    assert element.clicks == 0 and result.dry_run and not result.clicked


def test_clicker_reports_a_failed_click_instead_of_raising():
    result = ElementClicker().perform( FakeElement( fail_times=1 ))
    assert not result.clicked and "intercepted" in result.reason


def test_retry_decorator_retries_a_blocked_click_then_stops():
    clock, element = FakeClock(), FakeElement( fail_times=1 )
    result = RetryingClickAction( ElementClicker(), clock, attempts=3, pause_seconds=0.5 ).perform( element )
    assert result.clicked and element.clicks == 1 and clock.slept == [ 0.5 ]


def test_retry_decorator_gives_up_after_its_attempts():
    clock, element = FakeClock(), FakeElement( fail_times=9 )
    result = RetryingClickAction( ElementClicker(), clock, attempts=3 ).perform( element )
    assert not result.clicked and len( clock.slept ) == 2


def test_parts_factory_wraps_the_real_clicker_but_not_the_dry_run():
    parts = DefaultClickPartsFactory( default_registry(), FakeClock())
    assert isinstance( parts.click_action( True ), DryRunClicker )
    assert isinstance( parts.click_action( False ), RetryingClickAction )


# -- attaching ------------------------------------------------------------------

def test_attacher_assembles_a_session_that_lists_tabs_and_detaches_without_quitting():
    driver = FakeDriver({ "h1": "chrome://newtab/", "h2": "https://secure.53.com/olb/login" })
    login  = FakeElement( "Log In" )

    class Drivers:                                  # IDriverFactory
        def attached( self, endpoint ):
            assert endpoint.address == "127.0.0.1:9333"
            return driver

    class Managers:                                 # IBrowserManagerFactory
        def wrap( self, d ):
            return FakeManager( d, { BUTTON_LIKE: [ login ]})

    session = ManagerSessionAttacher( Drivers(), Managers(), StopDriverServiceDetach()).attach(
        DebugEndpoint( port=9333 ))
    assert [ t.url for t in session.tabs()] == [ "chrome://newtab/", "https://secure.53.com/olb/login" ]
    assert driver.current_window_handle == "h1"          # tabs() put focus back
    session.focus( "h2" )
    assert session.find( BUTTON_LIKE ) == [ login ]
    session.release()
    assert driver.service.stopped and not driver.quit_called


# -- probing --------------------------------------------------------------------

class FailingFetcher:                               # IJsonFetcher
    def get_json( self, url, timeout_seconds ):
        raise ConnectionRefusedError( "refused" )


class AnsweringFetcher:                             # IJsonFetcher
    def get_json( self, url, timeout_seconds ):
        return { "Browser": "Chrome/156" }


def test_probe_says_unreachable_rather_than_raising():
    check = DevToolsEndpointProbe( FailingFetcher()).check( DebugEndpoint())
    assert not check.reachable and "nothing answered at 127.0.0.1:9222" in check.reason


def test_probe_reads_the_browser_version():
    assert DevToolsEndpointProbe( AnsweringFetcher()).check( DebugEndpoint()).browser == "Chrome/156"


def test_readiness_gives_up_after_its_timeout_without_really_waiting():
    clock = FakeClock()
    check = PollingReadinessWaiter( DevToolsEndpointProbe( FailingFetcher()), clock,
                                    timeout_seconds=2.0, interval_seconds=0.5 ).wait( DebugEndpoint())
    assert not check.reachable and len( clock.slept ) == 4


# -- launching ------------------------------------------------------------------

def test_command_builder_is_immutable_and_refuses_an_incomplete_command():
    base = ChromeCommandBuilder().executable( "chrome.exe" ).profile( r"C:\p" ).flag( "--no-first-run" )
    one  = base.debugging_port( 9222 ).url( "https://a.example/" ).build()
    assert one == [ "chrome.exe", "--remote-debugging-port=9222", r"--user-data-dir=C:\p",
                    "--no-first-run", "https://a.example/" ]
    with pytest.raises( ValueError, match="debugging_port, url" ):
        base.build()                                 # the base was not changed by building `one`


def test_launcher_spawns_the_built_command_and_never_raises():
    spawned = []

    class Spawner:                                   # IProcessSpawner
        def spawn( self, argv ):
            spawned.append( argv )

    class Broken:
        def spawn( self, argv ):
            raise FileNotFoundError( "chrome.exe" )

    base = ChromeCommandBuilder().executable( "chrome.exe" ).profile( r"C:\p" )
    ok   = DebugChromeLauncher( base, Spawner()).launch( "https://a.example/", DebugEndpoint())
    bad  = DebugChromeLauncher( base, Broken()).launch( "https://a.example/", DebugEndpoint())
    assert ok.launched and spawned[ 0 ][ -1 ] == "https://a.example/"
    assert not bad.launched and "chrome.exe" in bad.reason


# -- catalog --------------------------------------------------------------------

def test_catalog_serves_the_registry_by_name():
    catalog = InMemoryTargetCatalog( ALL_TARGETS )
    assert catalog.get( "fifth_third_login" ) is FIFTH_THIRD_LOGIN
    assert catalog.get( "nope" ) is None and catalog.names() == [ "fifth_third_login" ]
