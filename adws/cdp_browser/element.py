"""CdpElement: IWorkflowElement over one DOM node held as a CDP remote object.

Everything is read from the node itself -- its text, attributes and the box
the DOM reports for it. A click goes to the centre of that box with
Input.dispatchMouseEvent (a real, trusted click), after checking with
elementFromPoint that nothing else is lying on top of it; if something is,
the node's own click() is used instead. No picture is ever taken.
"""
from __future__ import annotations

from typing import Any

from browser_workflows.errors import BrowserWorkflowError

from .interfaces import ICdpChannel

#: The object group every remote object of a turn lives in, released together.
GROUP = "cdp-workflow"

_BOX = """function () {
  this.scrollIntoView({ block: 'center', inline: 'center' });
  const r = this.getBoundingClientRect();
  const x = r.left + r.width / 2, y = r.top + r.height / 2;
  const hit = document.elementFromPoint(x, y);
  return { x, y, width: r.width, height: r.height, clear: !!hit && (hit === this || this.contains(hit)) };
}"""

_DISPLAYED = """function () {
  const r = this.getBoundingClientRect(), s = getComputedStyle(this);
  return !!(r.width && r.height) && s.visibility !== 'hidden' && s.display !== 'none';
}"""


class CdpElement:
    def __init__( self, channel: ICdpChannel, object_id: str ) -> None:
        self._cdp = channel
        self.object_id = object_id
        self._tag: str | None = None

    # -- reading ------------------------------------------------------------------

    @property
    def text( self ) -> str:
        return self._value( "function () { return (this.innerText ?? this.textContent ?? '').trim(); }" ) or ""

    @property
    def tag_name( self ) -> str:
        """Read once: a node's tag never changes, and a click that navigates
        away leaves nothing to ask afterwards."""
        if self._tag is None:
            self._tag = ( self._value( "function () { return this.tagName || ''; }" ) or "" ).lower()
        return self._tag

    @property
    def rect( self ) -> dict[ str, float ]:
        box = self._value( "function () { const r = this.getBoundingClientRect();"
                           " return { x: r.x, y: r.y, width: r.width, height: r.height }; }" )
        return box or { "x": 0.0, "y": 0.0, "width": 0.0, "height": 0.0 }

    def is_displayed( self ) -> bool:
        return bool( self._value( _DISPLAYED ))

    def is_enabled( self ) -> bool:
        return not self._value( "function () { return !!this.disabled; }" )

    def is_selected( self ) -> bool:
        return bool( self._value( "function () { return !!(this.checked || this.selected); }" ))

    def get_attribute( self, name: str ) -> str | None:
        found = self._value( "function (n) { return this.getAttribute(n); }", name )
        return None if found is None else str( found )

    # -- acting -------------------------------------------------------------------

    def click( self ) -> None:
        _ = self.tag_name
        box = self._value( _BOX )
        if not box or not box.get( "width" ) or not box.get( "height" ):
            raise BrowserWorkflowError( "the element has no size on the page, so it cannot be clicked" )
        if not box.get( "clear" ):
            self._value( "function () { this.click(); }" )
            return
        # No separate mouseMoved: Chrome holds its reply for a frame that a
        # background tab may not draw for seconds; a press moves the pointer anyway.
        x, y = box[ "x" ], box[ "y" ]
        for kind in ( "mousePressed", "mouseReleased" ):
            self._cdp.call( "Input.dispatchMouseEvent",
                            { "type": kind, "x": x, "y": y, "button": "left", "clickCount": 1 })

    def clear( self ) -> None:
        self._value( "function () { this.focus(); if ('value' in this) { this.value = '';"
                     " this.dispatchEvent(new Event('input', { bubbles: true })); } }" )

    def send_keys( self, *value: str ) -> None:
        self._value( "function () { this.focus(); }" )
        self._cdp.call( "Input.insertText", { "text": "".join( value )})

    def focus( self ) -> None:
        self._value( "function () { this.focus(); }" )

    # -- plumbing -----------------------------------------------------------------

    def _value( self, function: str, *args: Any ) -> Any:
        result = self._cdp.call( "Runtime.callFunctionOn", {
            "objectId":            self.object_id,
            "functionDeclaration": function,
            "arguments":           [{ "value": a } for a in args ],
            "returnByValue":       True,
            "awaitPromise":        False,
        })
        if "exceptionDetails" in result:
            raise BrowserWorkflowError( f"the page refused: { _describe( result[ 'exceptionDetails' ]) }" )
        return result.get( "result", {}).get( "value" )


def _describe( details: dict[ str, Any ]) -> str:
    return str( details.get( "exception", {}).get( "description" ) or details.get( "text", "a script error" ))[ :200 ]
