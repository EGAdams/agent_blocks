"""HeadingArrivalCheck: IArrivalCheck -- the title, the address or a heading
names the goal. Headings, not the page body: it is what a person reads first."""
from __future__ import annotations

from urllib.parse import urlparse

from ..interfaces.browser import ICashyBrowser
from ..models import GoalVocabulary


class HeadingArrivalCheck:
    def arrived( self, browser: ICashyBrowser, goal: GoalVocabulary ) -> bool:
        if not goal.arrived:
            return False
        seen = [ browser.title, urlparse( browser.current_url ).path.replace( "-", " " ).replace( "_", " " )]
        seen += [ e.text for e in browser.find( "h1, h2, [role=heading]" )[ :6 ]]
        said = " ".join( seen ).casefold()
        return any( word in said for word in goal.arrived )
