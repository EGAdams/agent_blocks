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
  return { x, y, width: r.width, height: r.height, clear: !!hit && (hit === this || this.contains(hit)),
           drawn: document.visibilityState === 'visible' };
}"""

_DISPLAYED = """function () {
  const r = this.getBoundingClientRect(), s = getComputedStyle(this);
  return !!(r.width && r.height) && s.visibility !== 'hidden' && s.display !== 'none';
}"""

#: A dropdown's own options, as data. Nothing here is a secret: a <select>'s choices are on the screen.
_OPTIONS = """function () {
  return Array.from(this.options || []).map(function (o, i) {
    return { position: i + 1, value: o.value, text: (o.text || '').trim().replace(/\\s+/g, ' ').slice(0, 160),
             selected: !!o.selected, disabled: !!o.disabled };
  });
}"""

#: Pick the option with exactly this value and tell the page, the way a person's choice does
#: (input, then change). Returns the list's value afterwards, or null when there is no such option.
_CHOOSE = """function (wanted) {
  const hit = Array.from(this.options || []).find(o => o.value === wanted);
  if (!hit || hit.disabled) return null;
  this.value = hit.value;
  this.dispatchEvent(new Event('input', { bubbles: true }));
  this.dispatchEvent(new Event('change', { bubbles: true }));
  return this.value;
}"""

#: Empty the field first, so filling replaces rather than appends. False means it holds no text at all.
_EMPTY = """function () {
  this.focus();
  if (this.isContentEditable) { this.textContent = ''; return true; }
  if (!('value' in this) || typeof this.value !== 'string') return false;
  this.value = '';
  this.dispatchEvent(new Event('input', { bubbles: true }));
  return true;
}"""

#: Tell the field its value outright, for a field that will not take typed keys at all (a date input
#: wants YYYY-MM-DD, a masked field may swallow the keys). The assignment goes through the prototype's
#: own setter, because a framework that watches its inputs installs one on the node itself and caches
#: the last value it saw there -- going round that is what makes React and friends notice the change.
_ASSIGN = """function (wanted) {
  if (this.isContentEditable) { this.textContent = wanted; }
  else {
    const proto = this instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
                : this instanceof HTMLInputElement ? HTMLInputElement.prototype
                : this instanceof HTMLSelectElement ? HTMLSelectElement.prototype : null;
    const own = proto && Object.getOwnPropertyDescriptor(proto, 'value');
    if (own && own.set) own.set.call(this, wanted);
    else if ('value' in this) this.value = wanted;
    else return null;
  }
  this.dispatchEvent(new Event('input', { bubbles: true }));
  this.dispatchEvent(new Event('change', { bubbles: true }));
  return this.isContentEditable ? (this.textContent || '') : String(this.value == null ? '' : this.value);
}"""

#: After typing: a change event (Input.insertText only fires input), then the value the field ended up with.
_SETTLE = """function () {
  this.dispatchEvent(new Event('change', { bubbles: true }));
  return this.isContentEditable ? (this.textContent || '') : String(this.value == null ? '' : this.value);
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
        # A page Chrome is not drawing (window covered, screen locked) drops a dispatched
        # mouse event without a word: the click "succeeds" and nothing happens. Measured on
        # Accela's Search link, whose postback ran from this.click() and never from the mouse.
        if not box.get( "clear" ) or box.get( "drawn" ) is False:
            self._value( "function () { this.click(); }" )
            return
        # No separate mouseMoved: Chrome holds its reply for a frame that a
        # background tab may not draw for seconds; a press moves the pointer anyway.
        x, y = box[ "x" ], box[ "y" ]
        for kind in ( "mousePressed", "mouseReleased" ):
            self._cdp.call( "Input.dispatchMouseEvent",
                            { "type": kind, "x": x, "y": y, "button": "left", "clickCount": 1 })

    def reveal( self ) -> None:
        """Open every closed `<details>` around the element and scroll it into view, so a control
        inside a collapsed section can be clicked like any other. Nothing is pressed."""
        self._value( "function () { for ( let n = this.parentElement; n; n = n.parentElement )"
                     " if ( n.tagName === 'DETAILS' && !n.open ) n.open = true;"
                     " this.scrollIntoView({ block: 'center' }); }" )

    def clear( self ) -> None:
        self._value( "function () { this.focus(); if ('value' in this) { this.value = '';"
                     " this.dispatchEvent(new Event('input', { bubbles: true })); } }" )

    def send_keys( self, *value: str ) -> None:
        self._value( "function () { this.focus(); }" )
        self._cdp.call( "Input.insertText", { "text": "".join( value )})

    def focus( self ) -> None:
        self._value( "function () { this.focus(); }" )

    # -- form controls ------------------------------------------------------------

    def options( self ) -> list[ dict[ str, Any ]]:
        """The <option>s of a dropdown, in order. A node that is not a list has none.

        An <option> is never clicked: Chrome gives it a 0x0 box (the open list is drawn by
        Windows, not the page), so every element resolver rejects it. The list is told its
        new value instead, which is what `choose` does."""
        return list( self._value( _OPTIONS ) or [])

    def choose( self, value: str ) -> str | None:
        """Select the option with exactly this value, firing input and change.
        Returns the list's value afterwards; None when it has no such (enabled) option."""
        chosen = self._value( _CHOOSE, value )
        return None if chosen is None else str( chosen )

    def value( self ) -> str:
        """What the field is showing now: its value, or its text when it is a node a person edits.
        A site's own dropdown keeps the chosen item here, which is how that choice is read back."""
        return str( self._value( "function () { return this.value != null ? String(this.value)"
                                " : (this.innerText ?? this.textContent ?? ''); }" ) or "" )

    def fill( self, text: str ) -> str:
        """Replace what is in the field with `text` and return the value it ended up with.

        Typing comes first, with Input.insertText -- real key input, so a framework watching for
        it sees it. A field that refuses keys outright (a date input, a masked field) is then told
        its value directly; that second attempt only wins if it got exactly what was asked for, or
        if typing got nothing at all, because a field that reformats what is typed is doing its job
        and its version is the one a person would have ended up with."""
        if not self._value( _EMPTY ):
            raise BrowserWorkflowError( "that element holds no text, so nothing could be typed into it" )
        if text:
            self._cdp.call( "Input.insertText", { "text": text })
        typed = str( self._value( _SETTLE ) or "" )
        if typed == text:
            return typed
        assigned = self._value( _ASSIGN, text )
        if assigned is None:
            return typed
        assigned = str( assigned )
        return assigned if ( assigned == text or not typed ) else typed

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
