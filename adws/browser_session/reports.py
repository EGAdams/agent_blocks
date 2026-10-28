"""IClickReporter and IDebugOpenReporter: the operator's one line. Wording only."""
from __future__ import annotations

from .models import ClickResult, ClickTarget, EndpointCheck, LocateResult, TabChoice

DEBUG_OPEN_ADW = "adws/adw_interface_browser_debug_open.py"
CLICK_ADW      = "adws/adw_interface_browser_click.py"


class PlainClickReporter:

    def unknown_target( self, name: str, known: list[ str ]) -> str:
        return f"No button named '{ name }'. Known: { ', '.join( known )}."

    def no_browser( self, port: int, check: EndpointCheck ) -> str:
        return ( f"No debuggable Chrome is listening on port { port } -- a window opened the ordinary way "
                 f"cannot be steered. Open the page with `uv run { DEBUG_OPEN_ADW } \"why\" --url <url>`, "
                 f"sign in there, then run this again." )

    def attach_failed( self, port: int, error: str ) -> str:
        return f"Found Chrome on port { port } but could not attach to it: { error }"

    def no_tab( self, target: ClickTarget, choice: TabChoice ) -> str:
        return ( f"No open https tab is on { target.host_suffix } (open tabs: "
                 f"{ ', '.join( choice.seen ) or 'none' }). Open the sign-in page first." )

    def not_found( self, target: ClickTarget, locate: LocateResult ) -> str:
        return ( f"On { target.host_suffix }, but could not find { target.description } "
                 f"(tried { ' then '.join( locate.tried )})." )

    def finished( self, target: ClickTarget, tab: TabChoice, locate: LocateResult, click: ClickResult ) -> str:
        if click.dry_run:
            return f"Dry run: found { target.description } on { tab.url } ({ locate.strategy }); did not click."
        if not click.clicked:
            return f"Found { target.description } but the click failed: { click.reason }"
        return f"Clicked { target.description } on { tab.url }."


class PlainDebugOpenReporter:

    def refused( self, reason: str ) -> str:
        return f"Could not open a browser: { reason }."

    def dry_run( self, url: str, port: int ) -> str:
        return f"Dry run: would open { url } in the debug Chrome on port { port }."

    def not_launched( self, reason: str ) -> str:
        return reason

    def never_ready( self, url: str, port: int ) -> str:
        return ( f"Started Chrome for { url }, but its debug port { port } never answered -- if an "
                 f"ordinary Chrome window had this profile open, close it and retry." )

    def opened( self, url: str, port: int, already_running: bool ) -> str:
        where = "a new tab of the debug Chrome" if already_running else "the debug Chrome"
        return ( f"Opened { url } in { where } (port { port }). When you have typed what you need, "
                 f"run `uv run { CLICK_ADW } \"why\"` to press the button." )
