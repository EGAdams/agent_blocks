"""A scripted page and clock for playback tests: no browser, no waiting."""
from __future__ import annotations

import re

from browser_session.models import TabInfo

from browser_workflows.models import ElementSummary
from browser_workflows.playback.locating import LABEL_SCOPE, TEXT_SCOPE

ID_SELECTOR = re.compile( r'^\[id="(.+)"\]$' )


class FakeClock:
    def __init__( self ) -> None:
        self.t, self.slept = 0.0, 0.0

    def now( self ) -> float:
        return self.t

    def sleep( self, seconds: float ) -> None:
        self.t += seconds
        self.slept += seconds


class FakeElement:
    def __init__( self, tag: str, selectors: tuple[ str, ... ] = (), text: str = "", attrs: dict | None = None,
                  displayed: bool = True, enabled: bool = True, selected: bool = False, page=None ) -> None:
        self.tag_name, self.selectors, self.text = tag, set( selectors ), text
        self.attrs, self.displayed, self.enabled, self.selected = attrs or {}, displayed, enabled, selected
        self.rect = { "width": 20.0, "height": 20.0 }
        self.typed: list[ str ] = []
        self.clicks = 0
        self.page = page
        self.on_click = None

    def is_displayed( self ) -> bool:
        return self.displayed

    def is_enabled( self ) -> bool:
        return self.enabled

    def is_selected( self ) -> bool:
        return self.selected

    def get_attribute( self, name: str ) -> str | None:
        return self.attrs.get( name )

    def click( self ) -> None:
        self.clicks += 1
        if self.tag_name == "input" and self.attrs.get( "type" ) == "checkbox":
            self.selected = not self.selected
        if self.on_click:
            self.on_click()

    def clear( self ) -> None:
        self.typed.clear()

    def send_keys( self, *value: str ) -> None:
        self.typed.extend( value )


class FakeBrowser:
    def __init__( self, url: str = "about:blank" ) -> None:
        self.elements: list[ FakeElement ] = []
        self.current_url, self.title = url, "Fake"
        self.visits: list[ str ] = []
        self.keys: list[ str ] = []
        self.selected: list[ str ] = []
        self.released = False
        self.find_calls = 0
        self.body = ""
        self.download_directory = ""

    def add( self, *elements: FakeElement ) -> "FakeBrowser":
        self.elements.extend( elements )
        return self

    def find( self, css_selector: str ) -> list[ FakeElement ]:
        self.find_calls += 1
        if css_selector == TEXT_SCOPE:
            return list( self.elements )
        if css_selector == LABEL_SCOPE:
            return [ e for e in self.elements if e.tag_name in ( "input", "select", "textarea", "button" )]
        if css_selector == "label[for]":
            return [ e for e in self.elements if e.tag_name == "label" and "for" in e.attrs ]
        m = ID_SELECTOR.match( css_selector )
        if m:
            return [ e for e in self.elements if e.attrs.get( "id" ) == m.group( 1 )]
        if css_selector.startswith( "!!" ):
            raise ValueError( "invalid selector" )
        return [ e for e in self.elements if css_selector in e.selectors ]

    def ready_state( self ) -> str:
        return "complete"

    def body_text( self ) -> str:
        return self.body

    def navigate( self, url: str ) -> None:
        self.visits.append( url )
        self.current_url = url

    def select_option( self, element, value: str ) -> None:
        self.selected.append( value )

    def press_key( self, element, key: str ) -> None:
        self.keys.append( key )

    def screenshot( self, path: str ) -> bool:
        return False

    def interactive_elements( self, limit: int = 60 ) -> list[ ElementSummary ]:
        return [ ElementSummary( tag=e.tag_name, text=e.text, css=sorted( e.selectors )[ 0 ] if e.selectors else e.tag_name,
                                 attrs={ k: v for k, v in e.attrs.items() if k != "value" })
                 for e in self.elements if e.displayed ][ :limit ]

    def tabs( self ) -> list[ TabInfo ]:
        return [ TabInfo( handle="h1", url=self.current_url )]

    def focus( self, handle: str ) -> None:
        pass

    def configure_downloads( self, directory: str ) -> None:
        self.download_directory = directory

    def release( self ) -> None:
        self.released = True


class ScriptedConsole:
    def __init__( self, answers: list[ str ] | None = None, secrets: list[ str ] | None = None ) -> None:
        self.answers, self.secrets, self.said = list( answers or []), list( secrets or []), []

    def say( self, text: str ) -> None:
        self.said.append( text )

    def ask( self, prompt: str ) -> str:
        self.said.append( prompt )
        return self.answers.pop( 0 ) if self.answers else ""

    def ask_secret( self, prompt: str ) -> str:
        self.said.append( prompt )
        return self.secrets.pop( 0 ) if self.secrets else ""
