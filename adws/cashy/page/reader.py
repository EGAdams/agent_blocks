"""DomPageReader: IDomReader -- what is on the page, read from the DOM.

The options come from the same script the playback diagnostics use (it
never reads a typed value), numbered in page order so "click the second
one" means what the person was shown.
"""
from __future__ import annotations

from ..interfaces.browser import ICashyBrowser
from ..models import BankProfile, OptionKind, PageOption, PageSnapshot

LIMIT = 200


def _kind( tag: str, attrs: dict[ str, str ]) -> OptionKind:
    if tag == "a":
        return "link"
    if tag == "select":
        return "list"
    if tag == "button" or attrs.get( "type" ) in ( "submit", "button", "reset" ):
        return "button"
    if tag in ( "input", "textarea" ):
        return "field"
    return "button" if attrs.get( "role", "" ) else "other"


class DomPageReader:
    def snapshot( self, browser: ICashyBrowser, bank: BankProfile | None ) -> PageSnapshot:
        seen: set[ tuple[ str, str ]] = set()
        options: list[ PageOption ] = []
        login_form = False
        for element in browser.interactive_elements( LIMIT ):
            attrs = element.attrs
            if element.tag == "input" and attrs.get( "type" ) == "password":
                login_form = True
            label = attrs.get( "aria-label" ) or attrs.get( "title" ) or attrs.get( "placeholder" ) or ""
            text  = element.text or label
            if not text:
                continue
            key = ( text.casefold(), attrs.get( "href", "" ))
            if key in seen:
                continue
            seen.add( key )
            options.append( PageOption( number=len( options ) + 1, kind=_kind( element.tag, attrs ), tag=element.tag,
                                        text=text, css=element.css, href=attrs.get( "href", "" ), label=label ))
        return PageSnapshot( url=browser.current_url, title=browser.title, options=options,
                             signed_in=_signed_in( options, login_form, bank ), login_form=login_form )


def _signed_in( options: list[ PageOption ], login_form: bool, bank: BankProfile | None ) -> bool | None:
    words = bank.signed_in_words if bank else [ "sign out", "log out", "log off", "logout" ]
    if any( w in o.text.casefold() for o in options for w in words ):
        return True
    return False if login_form else None
