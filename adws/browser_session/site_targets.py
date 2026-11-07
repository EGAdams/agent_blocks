"""Which button, on which site. Pure data -- `InMemoryTargetCatalog` serves it.

A new button is a new entry in `ALL_TARGETS`, nothing else: the locators,
tab selector and clicker already handle any of them.
"""
from __future__ import annotations

from .models import ClickTarget, LocatorSpec

#: Fifth Third's Angular login button. Its id (`fds-button-1`) and the
#: `_ngcontent-ng-c...` attributes are generated per build, so neither is
#: relied on: the visible label is what a person sees and what changes least.
FIFTH_THIRD_LOGIN = ClickTarget(
    name="fifth_third_login",
    description="the Log In button on Fifth Third's online banking sign-in page",
    host_suffix="53.com",
    locators=[
        LocatorSpec( kind="button_text", value="Log In" ),
        LocatorSpec( kind="css", value="button[fds-button][type='submit']" ),
    ],
)

#: The NOTES tab on the ROL dashboard, served over Tailscale from DESKTOP-2OBSQMC.
#: Its `data-target` names the panel it shows, so that is tried before the label.
DASHBOARD_NOTES_TAB = ClickTarget(
    name="dashboard_notes_tab",
    description="the NOTES tab on the ROL dashboard",
    host_suffix="desktop-2obsqmc.tailb8fc54.ts.net",
    locators=[
        LocatorSpec( kind="css", value="button.tab[data-nav='main'][data-target='notes']" ),
        LocatorSpec( kind="button_text", value="NOTES" ),
    ],
)

ALL_TARGETS: list[ ClickTarget ] = [ FIFTH_THIRD_LOGIN, DASHBOARD_NOTES_TAB ]
