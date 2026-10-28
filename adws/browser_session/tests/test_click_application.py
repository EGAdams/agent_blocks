"""BrowserClickApplication's orchestration with every interface faked:
where it stops, what it says, and that it always releases the browser."""
import sys
from pathlib import Path

ADWS_DIR = Path( __file__ ).resolve().parents[ 2 ]
if str( ADWS_DIR ) not in sys.path:
    sys.path.insert( 0, str( ADWS_DIR ))

import adw_interface_browser_click as adw                                       # noqa: E402
from browser_session.models import ( ClickResult, EndpointCheck, LocateResult,   # noqa: E402
                                     TabChoice )
from browser_session.reports import PlainClickReporter                          # noqa: E402
from browser_session.site_targets import ALL_TARGETS, FIFTH_THIRD_LOGIN         # noqa: E402
from browser_session.target_catalog import InMemoryTargetCatalog                # noqa: E402


class Phase:                                        # IPhase stand-in: the executor just returns `value`
    def __init__( self, name, value ):
        self.name, self.value = name, value


class Executor:                                     # IPhaseExecutor
    def __init__( self ):
        self.ran = []

    def execute( self, run, phase ):
        self.ran.append( phase.name )
        return phase.value


class Session:                                      # IBrowserSession
    released = False

    def release( self ):
        self.released = True


class Phases:                                       # IClickPhaseFactory
    def __init__( self, reachable=True, tab=True, element=True, clicked=True ):
        self.session = Session()
        self._reachable, self._tab, self._element, self._clicked = reachable, tab, element, clicked

    def request( self, engineer, request, log ):
        return Phase( "request", None )

    def resolve_target( self, name, log ):
        return Phase( "resolve_target", FIFTH_THIRD_LOGIN if name == "fifth_third_login" else None )

    def probe( self, endpoint, log ):
        return Phase( "probe", EndpointCheck( reachable=self._reachable ))

    def attach( self, endpoint, log ):
        return Phase( "attach", ( self.session, "" ))

    def select_tab( self, session, target, log ):
        return Phase( "select_tab", TabChoice( found=self._tab, url="https://secure.53.com/olb/login" ))

    def locate( self, session, target, log ):
        return Phase( "locate", ( object() if self._element else None,
                                  LocateResult( found=self._element, strategy="text:exact:log in",
                                                tried=[ "text:exact:log in" ])))

    def click( self, element, dry_run, log ):
        return Phase( "click", ClickResult( clicked=self._clicked and not dry_run, dry_run=dry_run ))


class NullLog:                                      # ILifecycleLog
    def created( self, obj ):
        pass

    def destroyed( self, obj ):
        pass


class Run:
    engineer = "test"


def _app( phases, executor ):
    return adw.BrowserClickApplication( None, None, None, executor, phases,
                                        InMemoryTargetCatalog( ALL_TARGETS ), PlainClickReporter(),
                                        None, None, logger=NullLog())


def _request( **kw ):
    base = dict( prompt="p", target="fifth_third_login", port=9222, dry_run=False,
                 message_out="", config="c", adw_id=None )
    base.update( kw )
    return adw.BrowserClickRequest( **base )


def test_happy_path_clicks_and_releases():
    phases, executor = Phases(), Executor()
    ok, line = _app( phases, executor )._click( Run(), _request(), None )
    assert ok and line.startswith( "Clicked the Log In button" )
    assert executor.ran[ -1 ] == "click" and phases.session.released


def test_unknown_target_stops_before_touching_a_browser():
    executor = Executor()
    ok, line = _app( Phases(), executor )._click( Run(), _request( target="nope" ), None )
    assert not ok and "No button named 'nope'" in line and "probe" not in executor.ran


def test_no_debug_chrome_says_how_to_open_one():
    ok, line = _app( Phases( reachable=False ), Executor())._click( Run(), _request(), None )
    assert not ok and "adw_interface_browser_debug_open.py" in line


def test_missing_button_still_releases_the_browser():
    phases = Phases( element=False )
    ok, line = _app( phases, Executor())._click( Run(), _request(), None )
    assert not ok and "could not find" in line and phases.session.released


def test_dry_run_is_accepted_without_clicking():
    ok, line = _app( Phases(), Executor())._click( Run(), _request( dry_run=True ), None )
    assert ok and line.startswith( "Dry run:" )
