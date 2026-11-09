"""BrowserAgent: the IAgentAdapter that lets ChatGPT and Toyota drive a browser on the Windows desktop. No model in the path.

The prompt is JSON written by code (ChatGPT turns "open the browser" into it; Toyota's plan does the same):

    {"browser": "open", "url": "https://example.com", "target": "chrome"}   # Chrome is the only authorized browser

    inspect    read_only        status | tabs | read | screenshot | form_controls | notes_state (note, reload)
    implement  workspace_write  open | navigate | refresh | select_tab | click | close | dashboard_notes
                                select_option | fill | download                           (workspace `browser`)

`form_controls` is how a form is operated without guessing: it lists the page's fields, dropdowns (with every
option) and buttons, each with the selector to pass back as `css`. `select_option` chooses an item in a dropdown
(by `option_text` or `value`) -- an item in a list is never clicked, because Chrome draws the open list outside
the page and nothing on it can be reached. It works a list the site draws itself too (role=combobox and the
like): the ADW opens that one and clicks the item inside it, which is the opposite act for the same request.
`fill` types `text` into one ordinary field, including a date or masked field that takes no typed keys.

`notes_state` opens one note on the dashboard's Notes page and reads what it blinks: the line, whether its text stays
visible while the background blinks, and the revision the page holds (`reload` first, to check what survived a
refresh). It changes nothing on the note. The Notes Review ADW runs it to check the page agrees with a saved edit.

`dashboard_notes` is the whole "open the browser and go to Notes": Chrome on the desktop, the ROL dashboard,
the NOTES tab pressed, and the page read back to confirm NOTES is the active tab. It takes no fields.

Each task becomes one run of `adws/adw_browser_control.py` (through `IBrowserCommands`), which is the Browser
Control ADW: it has its own execution id (the ADW id), lifecycle log and object trace. This adapter only checks the
request against a fixed table, never lets a value become an argv option, and turns the ADW's one JSON outcome into
an honest AgentOutcome: success means the ADW verified the operation, never that it was queued.

`download` is `click` for anything that saves a file, and it is a separate operation on purpose. Chrome writes a
downloaded file only when a folder has been set for the session, so a `click` on a `.tif` or a statement link
answered "Clicked '0337201.tif'." over an empty folder -- a true sentence about a failed job. `download` sets the
folder, presses the element, waits for Chrome to finish writing and reports the file's name, size and first bytes;
nothing arriving is reported as nothing arriving, never as a download. `click` now refuses a file link outright and
names this operation. It takes the same locators as `click` (`css` best, else `label` or `text`) -- or `url`, a
bare address of the file, asked for in a tab of its own with the same signed-in session -- and because it
writes to EG's disk it is an `implement`, never an `inspect`. Each file is saved under a name nothing else has
(Chrome itself would overwrite), with its SHA-256 and real type, and its pages rendered; the injected
`IDownloadPublisher` then uploads it to the gateway, which gives it the `file_id` that `browser_view_file` takes.

A click whose words look consequential (pay, send, delete ...) is refused by the ADW unless the request carries
`"confirm": true`, which ChatGPT may set only after EG said so. Nothing here reads cookies, storage or passwords.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .models import AgentOutcome, ClaimedTask, OutcomeKind
from .ports import IClock, Signal

PERFORMER = "browser_control"
READS     = frozenset({ "status", "tabs", "read", "screenshot", "form_controls", "notes_state" })
WRITES    = frozenset({ "open", "navigate", "refresh", "select_tab", "click", "close", "dashboard_notes",
                        "select_option", "fill", "download" })
#: request key -> the ADW flag it becomes (the ADW spells a two-word flag with a dash: --option-text).
#: `target` is the browser (Chrome only); `browser` already names the operation.
FLAGS     = { "target": "browser", "url": "url", "tab": "tab", "text": "text", "label": "label", "css": "css",
              "option_text": "option-text", "value": "value", "limit": "limit", "note": "note", "sample_ms": "sample-ms" }
#: request keys that are on/off switches: passed to the ADW by name alone, and only when true.
SWITCHES  = { "reload": "reload" }
REQUIRED  = { "navigate": ("url",), "select_tab": ("tab",), "close": ("tab",), "fill": ("text",), "notes_state": ("note",) }
NOTE_NAME = re.compile( r"[A-Za-z0-9][A-Za-z0-9._-]{0,200}\.md" )
MAX_VALUE = 500


class BrowserCommandFailed( Exception ):
    """The Browser Control ADW gave no outcome (it crashed, timed out, or wrote nothing)."""


class IRunningBrowserCommand( Protocol ):
    def poll( self ) -> dict | None: ...        # None while running; the ADW's outcome once done
    def stop( self ) -> None: ...


class IBrowserCommands( Protocol ):
    def start( self, operation: str, flags: dict[ str, str ], confirm: bool ) -> IRunningBrowserCommand: ...


class IDownloadPublisher( Protocol ):
    """Hands a download's saved files to the gateway for viewing; returns the details with file ids. Never raises."""
    def publish( self, task: ClaimedTask, details: dict ) -> dict: ...


class BrowserAgent:
    def __init__( self, commands: IBrowserCommands, clock: IClock, poll_seconds: float = 0.5, deadline_seconds: float = 120.0,
                  publisher: IDownloadPublisher | None = None ) -> None:
        self._commands, self._clock, self._poll, self._deadline = commands, clock, poll_seconds, deadline_seconds
        self._publisher = publisher

    def run( self, task: ClaimedTask, workdir: Path, keep_alive: Callable[[], Signal ]) -> AgentOutcome:
        try:
            request = json.loads( task.prompt )
        except ValueError:
            request = None
        if not isinstance( request, dict ) or not isinstance( request.get( "browser" ), str ):
            return self._refused( 'a browser task is JSON, e.g. {"browser": "open", "url": "https://example.com"}; operations: '
                                  + ", ".join( sorted( READS ) + sorted( WRITES )))
        what = request[ "browser" ].removeprefix( "browser." )
        expected = "inspect" if what in READS else "implement" if what in WRITES else None
        if expected is None:
            return self._refused( f"the browser does not offer { what!r}" )
        if expected != task.operation:
            return self._refused( f"{ what } runs as { expected }, not { task.operation }" )
        if str( request.get( "target", "chrome" )).strip().lower() not in ( "chrome", "default", "google chrome" ):
            return self._refused( "Chrome is the only authorized browser; no other browser is launched" )
        unknown = set( request ) - set( FLAGS ) - set( SWITCHES ) - { "browser", "confirm" }
        if unknown:
            return self._refused( "unknown field(s): " + ", ".join( sorted( str( k )[ :20 ] for k in unknown )))
        flags: dict[ str, str ] = {}
        for key, flag in FLAGS.items():
            value = request.get( key )
            # An empty `text` is how a field is emptied, so `fill` keeps it; everywhere else it means nothing.
            if value is None or ( value == "" and not ( key == "text" and what == "fill" )):
                continue
            if not isinstance( value, ( str, int )) or isinstance( value, bool ) or len( str( value )) > MAX_VALUE:
                return self._refused( f"{ key } must be short text" )
            flags[ flag ] = str( value )
        for key, flag in SWITCHES.items():
            if key in request and not isinstance( request[ key ], bool ):
                return self._refused( f"{ key } is true or false" )
            if request.get( key ) is True:
                flags[ flag ] = ""                      # the command adapter passes an empty value as a bare switch
        if "note" in flags and not NOTE_NAME.fullmatch( flags[ "note" ]):
            return self._refused( "note is a note's file name, e.g. 2026-11-03_draft.md" )
        missing = [ k for k in REQUIRED.get( what, ()) if FLAGS[ k ] not in flags ]
        if missing:
            return self._refused( f"{ what } needs: " + ", ".join( missing ))
        if what == "click" and not any( k in flags for k in ( "text", "label", "css" )):
            return self._refused( "click needs text, label or css" )
        if what == "download" and not any( k in flags for k in ( "text", "label", "css", "url" )):
            return self._refused( "download needs text, label or css (something on the page to press), or url (a bare address)" )
        if what in ( "select_option", "fill" ) and not any( k in flags for k in ( "css", "label" )):
            return self._refused( f"{ what } needs css (from form_controls, best) or label; for { what }, text is not a locator" )
        if what == "select_option" and not any( k in flags for k in ( "option-text", "value" )):
            return self._refused( "select_option needs option_text (what the item says) or value" )
        if keep_alive() != "continue":
            return AgentOutcome( kind=OutcomeKind.CANCELLED, performer=PERFORMER, summary="cancelled before it started" )
        try:
            answer, signal = self._wait( self._commands.start( what, flags, request.get( "confirm" ) is True ), what, keep_alive )
        except BrowserCommandFailed as exc:
            return self._failed( f"the Browser Control ADW gave no outcome ({ exc })", what )
        if signal == "lost":
            return AgentOutcome( kind=OutcomeKind.LEASE_LOST, performer=PERFORMER, summary="the gateway took the lease back" )
        if signal == "cancel" and what in READS:
            return AgentOutcome( kind=OutcomeKind.CANCELLED, performer=PERFORMER, summary="cancelled before it finished" )
        outcome = self._outcome( answer, what )
        if what == "download" and outcome.kind == OutcomeKind.SUCCEEDED:
            outcome = self._published( task, outcome )
        return outcome

    def _published( self, task: ClaimedTask, outcome: AgentOutcome ) -> AgentOutcome:
        """A saved file ChatGPT cannot open is half a download: publish it, and say plainly when that failed."""
        if self._publisher is None:
            details = { **outcome.details, "inspectable": False,
                        "inspect_problem": "this worker has no gateway publisher wired, so the file is only on the desktop" }
            return outcome.model_copy( update={ "details": details })
        details = self._publisher.publish( task, dict( outcome.details ))
        summary, body = outcome.summary, outcome.body
        if details.get( "inspectable" ):
            files = details.get( "files" ) or []
            ids = ", ".join( f"{ f.get( 'name' )} = { f.get( 'file_id' )}" for f in files if f.get( "file_id" ))
            body = f"{ body }\n\nView with browser_view_file: { ids }"
            held = [ f for f in files if f.get( "published" ) is False ]
            if held:
                body += f"\n{ len( held ) } more file(s) are on the desktop only (one download publishes at most 20)."
        else:
            body = f"{ body }\n\n{ details.get( 'inspect_problem', 'the file could not be published for viewing' )}"
        return outcome.model_copy( update={ "details": details, "summary": summary, "body": body })

    def _wait( self, running: IRunningBrowserCommand, what: str, keep_alive: Callable[[], Signal ]) -> tuple[ dict, Signal ]:
        """Heartbeat until the ADW answers. A read may be stopped; a change that has started runs to its end."""
        deadline, seen = self._clock.monotonic() + self._deadline, "continue"
        while True:
            answer = running.poll()
            if answer is not None:
                return answer, seen
            if self._clock.monotonic() >= deadline:
                running.stop()
                raise BrowserCommandFailed( "timed out" )
            signal = keep_alive()
            if signal != "continue":
                seen = signal
                if what in READS:
                    running.stop()
                    return {}, signal
            self._clock.sleep( self._poll )

    @staticmethod
    def _outcome( answer: dict, what: str ) -> AgentOutcome:
        data     = answer.get( "data" ) if isinstance( answer.get( "data" ), dict ) else {}
        line     = str( answer.get( "line" ) or "" )[ :1000 ]
        verified = bool( answer.get( "ok" )) and data.get( "verified" ) is True
        details  = { "browser": what, "ok": bool( answer.get( "ok" )), "verified": verified, "adw_id": answer.get( "adw_id" ), **data }
        body     = line + ( "\n\n" + str( data.get( "text" )) if what == "read" and data.get( "text" ) else "" )
        if verified:
            return AgentOutcome( kind=OutcomeKind.SUCCEEDED, performer=PERFORMER, summary=line[ :300 ], body=body, details=details )
        # A refusal (consequential click, no such tab) is final: repeating the same request gives the same answer.
        return AgentOutcome( kind=OutcomeKind.FAILED, performer=PERFORMER, summary=( line or "the browser operation did not complete" )[ :300 ],
                             body=body, retryable=False, details=details )

    @staticmethod
    def _failed( summary: str, what: str ) -> AgentOutcome:
        return AgentOutcome( kind=OutcomeKind.FAILED, performer=PERFORMER, summary=summary[ :300 ], body=summary, retryable=False,
                             details={ "browser": what, "ok": False, "verified": False } )

    @staticmethod
    def _refused( summary: str ) -> AgentOutcome:
        return AgentOutcome( kind=OutcomeKind.REFUSED, performer=PERFORMER, summary=f"refused by the worker: { summary }" )
