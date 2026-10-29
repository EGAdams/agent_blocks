"""CdpWorkflowBrowser: IBrowserAdapter spoken over the DevTools protocol.

One page channel (the tab being driven) and, opened only when downloads are
configured, one browser channel -- kept open until release, because Chrome
forgets a download folder when the session that set it goes away.

Beyond IBrowserAdapter it offers the three things a person steering by
conversation needs: `go_back`, `open_tab` and `target_id`.
"""
from __future__ import annotations

import json
import time
from typing import Any

from browser_session.models import DebugEndpoint, TabInfo
from browser_workflows.errors import BrowserWorkflowError
from browser_workflows.interfaces.browser import IWorkflowElement
from browser_workflows.models import ElementSummary
from browser_workflows.playback.selenium_browser import INTERACTIVE_JS

from .channel import CdpCommandError
from .element import GROUP, CdpElement
from .interfaces import CdpTarget, ICdpChannel, ICdpConnector, ITargetDirectory
from .targets import pinned

#: key -> ( code, windowsVirtualKeyCode, text typed by it )
KEYS: dict[ str, tuple[ str, int, str ]] = {
    "Enter":     ( "Enter",     13, "\r" ),
    "Tab":       ( "Tab",        9, "" ),
    "Escape":    ( "Escape",    27, "" ),
    "Backspace": ( "Backspace",  8, "" ),
    "ArrowDown": ( "ArrowDown", 40, "" ),
    "ArrowUp":   ( "ArrowUp",   38, "" ),
    "Space":     ( "Space",     32, " " ),
    " ":         ( "Space",     32, " " ),
}

#: The flag `_mark` leaves on a document about to be navigated away from.
_MARK = "__cdpWorkflowLeaving"

_SELECT = """function (wanted) {
  const options = Array.from(this.options || []);
  const hit = options.find(o => o.value === wanted) || options.find(o => o.text.trim() === wanted);
  if (!hit) return false;
  this.value = hit.value;
  this.dispatchEvent(new Event('input', { bubbles: true }));
  this.dispatchEvent(new Event('change', { bubbles: true }));
  return true;
}"""


class CdpWorkflowBrowser:
    def __init__( self, endpoint: DebugEndpoint, target: CdpTarget,
                  directory: ITargetDirectory, connector: ICdpConnector,
                  load_timeout_seconds: float = 30.0 ) -> None:
        self._endpoint  = endpoint
        self._directory = directory
        self._connector = connector
        self._timeout   = load_timeout_seconds
        self._target    = target
        self._page      = self._open( target )
        self._browser: ICdpChannel | None = None

    # -- extras -------------------------------------------------------------------

    @property
    def target_id( self ) -> str:
        return self._target.id

    def go_back( self ) -> None:
        self._mark()
        self._evaluate( "history.back()" )
        self._wait_replaced( allow_same_document=True )

    def open_tab( self, url: str ) -> None:
        self._switch( self._directory.open_page( self._endpoint, url ))
        self._directory.activate( self._endpoint, self._target.id )
        self._wait_loaded()

    # -- IElementQuery ------------------------------------------------------------

    def find( self, css_selector: str ) -> list[ IWorkflowElement ]:
        result = self._page.call( "Runtime.evaluate", {
            "expression":  f"Array.from(document.querySelectorAll({ json.dumps( css_selector ) }))",
            "objectGroup": GROUP,
        })
        if "exceptionDetails" in result:
            return []                                   # an invalid selector finds nothing
        array_id = result.get( "result", {}).get( "objectId" )
        if not array_id:
            return []
        props = self._page.call( "Runtime.getProperties", { "objectId": array_id, "ownProperties": True })
        found = [ p for p in props.get( "result", []) if p.get( "name", "" ).isdigit()
                  and p.get( "value", {}).get( "objectId" )]
        found.sort( key=lambda p: int( p[ "name" ]))
        return [ CdpElement( self._page, p[ "value" ][ "objectId" ]) for p in found ]

    # -- IPageState ---------------------------------------------------------------

    @property
    def current_url( self ) -> str:
        return str( self._evaluate( "location.href" ) or "" )

    @property
    def title( self ) -> str:
        return str( self._evaluate( "document.title" ) or "" )

    def ready_state( self ) -> str:
        try:
            return str( self._evaluate( "document.readyState" ) or "" )
        except BrowserWorkflowError:
            return "loading"                            # the document was mid-replacement

    def body_text( self ) -> str:
        return str( self._evaluate( "document.body ? document.body.innerText : ''" ) or "" )

    # -- IPageNavigator -----------------------------------------------------------

    def navigate( self, url: str ) -> None:
        self._mark()
        result = self._page.call( "Page.navigate", { "url": url })
        if result.get( "errorText" ):
            raise BrowserWorkflowError( f"could not open { url }: { result[ 'errorText' ]}" )
        # No loaderId means a same-document move (a #fragment): nothing is replaced.
        self._wait_replaced( allow_same_document=not result.get( "loaderId" ))

    # -- IFormControls ------------------------------------------------------------

    def select_option( self, element: IWorkflowElement, value: str ) -> None:
        if not isinstance( element, CdpElement ):
            raise BrowserWorkflowError( "select_option needs an element this browser found" )
        chosen = self._page.call( "Runtime.callFunctionOn", {
            "objectId": element.object_id, "functionDeclaration": _SELECT,
            "arguments": [{ "value": value }], "returnByValue": True,
        }).get( "result", {}).get( "value" )
        if not chosen:
            raise BrowserWorkflowError( f"the list has no option { value!r}" )

    def press_key( self, element: IWorkflowElement | None, key: str ) -> None:
        if key not in KEYS:
            raise BrowserWorkflowError( f"unsupported key { key!r}; supported: { ', '.join( sorted( KEYS ))}" )
        if isinstance( element, CdpElement ):
            element.focus()
        code, vk, typed = KEYS[ key ]
        name = "Enter" if code == "Enter" else ( " " if code == "Space" else key )
        down: dict[ str, Any ] = { "type": "keyDown" if typed else "rawKeyDown", "key": name, "code": code,
                                   "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk }
        if typed:
            down[ "text" ] = typed
        self._page.call( "Input.dispatchKeyEvent", down )
        self._page.call( "Input.dispatchKeyEvent", { "type": "keyUp", "key": name, "code": code,
                                                     "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk })

    # -- IPageInspector -----------------------------------------------------------

    def screenshot( self, path: str ) -> bool:
        """Never: this browser reads the DOM, it does not take pictures."""
        return False

    def interactive_elements( self, limit: int = 60 ) -> list[ ElementSummary ]:
        rows = self._evaluate( f"(function () {{ { INTERACTIVE_JS } }})({ int( limit ) })" ) or []
        return [ ElementSummary.model_validate( r ) for r in rows ]

    # -- ITabs --------------------------------------------------------------------

    def tabs( self ) -> list[ TabInfo ]:
        return [ TabInfo( handle=t.id, url=t.url ) for t in self._directory.pages( self._endpoint )]

    def focus( self, handle: str ) -> None:
        if handle == self._target.id:
            return
        match = [ t for t in self._directory.pages( self._endpoint ) if t.id == handle ]
        if not match:
            raise BrowserWorkflowError( f"no open tab { handle }" )
        self._switch( match[ 0 ])
        self._directory.activate( self._endpoint, handle )

    # -- IDownloadSettings --------------------------------------------------------

    def configure_downloads( self, directory: str ) -> None:
        if self._browser is None:
            self._browser = self._connector.connect( self._directory.browser_socket( self._endpoint ))
        try:
            self._browser.call( "Browser.setDownloadBehavior",
                                { "behavior": "allow", "downloadPath": directory, "eventsEnabled": True })
        except CdpCommandError as exc:
            raise BrowserWorkflowError( f"cannot configure downloads to { directory }: { exc }" ) from exc

    # -- release ------------------------------------------------------------------

    def release( self ) -> None:
        """Close the sockets. The tabs and the browser stay exactly as they are."""
        for channel in ( self._page, self._browser ):
            if channel is None:
                continue
            try:
                if channel is self._page:
                    channel.call( "Runtime.releaseObjectGroup", { "objectGroup": GROUP }, timeout_seconds=2.0 )
            except BrowserWorkflowError:
                pass
            finally:
                channel.close()
        self._browser = None

    # -- plumbing -----------------------------------------------------------------

    def _open( self, target: CdpTarget ) -> ICdpChannel:
        if not target.webSocketDebuggerUrl:
            raise BrowserWorkflowError( f"tab { target.id } is already being debugged by another client" )
        channel = self._connector.connect( pinned( target.webSocketDebuggerUrl, self._endpoint ))
        channel.call( "Page.enable" )
        return channel

    def _switch( self, target: CdpTarget ) -> None:
        old = self._page
        self._page, self._target = self._open( target ), target
        try:
            old.call( "Runtime.releaseObjectGroup", { "objectGroup": GROUP }, timeout_seconds=2.0 )
        except BrowserWorkflowError:
            pass
        old.close()

    def _evaluate( self, expression: str ) -> Any:
        result = self._page.call( "Runtime.evaluate", { "expression": expression, "returnByValue": True })
        if "exceptionDetails" in result:
            details = result[ "exceptionDetails" ]
            raise BrowserWorkflowError( str( details.get( "exception", {}).get( "description" )
                                             or details.get( "text", "script error" ))[ :200 ])
        return result.get( "result", {}).get( "value" )

    def _wait_loaded( self ) -> None:
        deadline = time.monotonic() + self._timeout
        time.sleep( 0.1 )
        while time.monotonic() < deadline:
            if self.ready_state() == "complete":
                return
            time.sleep( 0.2 )
        raise BrowserWorkflowError( f"the page did not finish loading within { self._timeout:.0f }s" )

    def _mark( self ) -> None:
        """Tag the current document, so a wait can tell when it has been replaced
        (until then the old one still reports readyState "complete")."""
        try:
            self._evaluate( f"window.{ _MARK } = true" )
        except BrowserWorkflowError:
            pass

    def _wait_replaced( self, allow_same_document: bool ) -> None:
        """Until a new, fully loaded document is in the tab. A move that may stay
        in the same document (history.back() to a #fragment) is accepted once
        the page has been complete for a second without being replaced."""
        start    = time.monotonic()
        deadline = start + self._timeout
        while time.monotonic() < deadline:
            time.sleep( 0.1 )
            try:
                replaced = not self._evaluate( f"!!window.{ _MARK }" )
            except BrowserWorkflowError:
                continue                                # mid-replacement
            settled_in_place = allow_same_document and time.monotonic() - start > 1.0
            if ( replaced or settled_in_place ) and self.ready_state() == "complete":
                return
        raise BrowserWorkflowError( f"the page did not finish loading within { self._timeout:.0f }s" )
