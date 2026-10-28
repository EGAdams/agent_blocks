"""IBrowserAdapter over selenium-headless's WebBrowserManager, attached to the
user's debug Chrome. Selenium itself is imported only inside the methods
that need its helpers, so tests and --help pay nothing for it.

Releasing stops chromedriver (the injected IDetachPolicy). It never quits:
the window is the user's.
"""
from __future__ import annotations

from typing import Any, Protocol

from browser_session.interfaces import IBrowserManager, IBrowserManagerFactory, IDetachPolicy, IDriverFactory
from browser_session.models import DebugEndpoint, TabInfo

from ..errors import BrowserWorkflowError
from ..interfaces.browser import IBrowserAdapter, IWorkflowElement
from ..models import ElementSummary

KEYS = { "Enter": "ENTER", "Tab": "TAB", "Escape": "ESCAPE", "Backspace": "BACKSPACE", "ArrowDown": "ARROW_DOWN",
         "ArrowUp": "ARROW_UP", "Space": "SPACE", " ": "SPACE" }

#: Lists what a person could act on, with a selector that finds each again.
INTERACTIVE_JS = r"""
const limit = arguments[0];
const sel = 'a[href], button, input:not([type=hidden]), select, textarea, [role=button], [role=link], [role=menuitem], [role=tab]';
const cssFor = (el) => {
  if (el.id && !/\d{4,}|[:.]/.test(el.id)) return '#' + CSS.escape(el.id);
  for (const a of ['data-testid', 'name', 'aria-label']) {
    const v = el.getAttribute(a);
    if (v) { const s = `${el.tagName.toLowerCase()}[${a}="${CSS.escape(v)}"]`;
             if (document.querySelectorAll(s).length === 1) return s; }
  }
  const parts = [];
  for (let n = el; n && n.nodeType === 1 && n !== document.body; n = n.parentElement) {
    let i = 1; for (let s = n.previousElementSibling; s; s = s.previousElementSibling) if (s.tagName === n.tagName) i++;
    parts.unshift(`${n.tagName.toLowerCase()}:nth-of-type(${i})`);
  }
  return 'body > ' + parts.join(' > ');
};
const out = [];
for (const el of document.querySelectorAll(sel)) {
  const r = el.getBoundingClientRect();
  if (!r.width || !r.height || getComputedStyle(el).visibility === 'hidden') continue;
  const attrs = {};
  for (const a of ['id', 'name', 'type', 'aria-label', 'placeholder', 'title', 'data-testid', 'href'])
    if (el.getAttribute(a)) attrs[a] = el.getAttribute(a).slice(0, 120);
  // A field's typed value is never read: it may be a secret. A button's caption is fine.
  const caption = el.tagName === 'INPUT' ? (['submit', 'button', 'reset'].includes(el.type) ? el.value : '') : el.innerText;
  const text = (caption || '').trim().replace(/\s+/g, ' ').slice(0, 120);
  out.push({ tag: el.tagName.toLowerCase(), text, css: cssFor(el), attrs });
  if (out.length >= limit) break;
}
return out;
"""


class IScriptingDriver( Protocol ):
    """The slice of a Selenium WebDriver this adapter uses beyond IWindowDriver."""

    current_url: str
    title: str

    def get( self, url: str ) -> None: ...

    def execute_script( self, script: str, *args: Any ) -> Any: ...

    def save_screenshot( self, path: str ) -> bool: ...


class SeleniumWorkflowBrowser:
    def __init__( self, manager: IBrowserManager, detach: IDetachPolicy ) -> None:
        self._manager = manager
        self._detach  = detach

    @property
    def _driver( self ) -> IScriptingDriver:
        return self._manager.driver

    # -- IElementQuery / IPageState / IPageNavigator ---------------------------

    def find( self, css_selector: str ) -> list[ IWorkflowElement ]:
        return list( self._manager.find_elements( css_selector ))

    @property
    def current_url( self ) -> str:
        return self._driver.current_url

    @property
    def title( self ) -> str:
        return self._driver.title

    def ready_state( self ) -> str:
        return str( self._driver.execute_script( "return document.readyState" ))

    def body_text( self ) -> str:
        return str( self._driver.execute_script( "return document.body ? document.body.innerText : ''" ) or "" )

    def navigate( self, url: str ) -> None:
        self._driver.get( url )

    # -- IFormControls ---------------------------------------------------------

    def select_option( self, element: IWorkflowElement, value: str ) -> None:
        from selenium.webdriver.support.select import Select      # noqa: PLC0415  (see module docstring)
        select = Select( element )
        try:
            select.select_by_value( value )
        except Exception:
            select.select_by_visible_text( value )

    def press_key( self, element: IWorkflowElement | None, key: str ) -> None:
        from selenium.webdriver.common.action_chains import ActionChains   # noqa: PLC0415
        from selenium.webdriver.common.keys import Keys                   # noqa: PLC0415
        name = KEYS.get( key, key.upper())
        code = getattr( Keys, name, None )
        if code is None:
            raise BrowserWorkflowError( f"unknown key { key!r}" )
        if element is not None:
            element.send_keys( code )
        else:
            ActionChains( self._driver ).send_keys( code ).perform()

    # -- IPageInspector --------------------------------------------------------

    def screenshot( self, path: str ) -> bool:
        return bool( self._driver.save_screenshot( path ))

    def interactive_elements( self, limit: int = 60 ) -> list[ ElementSummary ]:
        rows = self._driver.execute_script( INTERACTIVE_JS, limit ) or []
        return [ ElementSummary.model_validate( r ) for r in rows ]

    # -- ITabs -----------------------------------------------------------------

    def tabs( self ) -> list[ TabInfo ]:
        driver, current = self._driver, self._driver.current_window_handle
        found = []
        for handle in driver.window_handles:
            driver.switch_to.window( handle )
            found.append( TabInfo( handle=handle, url=driver.current_url ))
        driver.switch_to.window( current )
        return found

    def focus( self, handle: str ) -> None:
        self._driver.switch_to.window( handle )

    def release( self ) -> None:
        self._detach.detach( self._driver )


class ManagerBrowserSessionProvider:
    """IBrowserSessionProvider: driver factory -> WebBrowserManager -> adapter."""

    def __init__( self, drivers: IDriverFactory, managers: IBrowserManagerFactory, detach: IDetachPolicy ) -> None:
        self._drivers  = drivers
        self._managers = managers
        self._detach   = detach

    def attach( self, endpoint: DebugEndpoint ) -> IBrowserAdapter:
        return SeleniumWorkflowBrowser( self._managers.wrap( self._drivers.attached( endpoint )), self._detach )
