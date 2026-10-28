"""browser_session -- drive a browser the user already has open.

The browser-open ADW hands a link to Windows and forgets it; nothing can steer
that window afterwards. This package is the other half: a Chrome started with
a DevTools debug port (by `adw_interface_browser_debug_open.py`) can be
re-attached to by any later run, so a person can type their own password into
a real window and a workflow can press the button after them.

Program to the interface: every collaborator is declared in `interfaces/`
(one module per concern, every name `I`-prefixed) and every class below is
written against those, never against each other. One module, one job:

Data
  models               the values that cross between the pieces (pydantic)
  site_targets         which button, on which site -- pure data
  target_catalog       ITargetCatalog over that data

Finding the element
  text_matchers        ITextMatcher        Strategy: exact / contains
  element_specs        IElementSpecification  Specification + Composite: shown, enabled
  locators             IElementLocator     Strategy per way of finding, + Chain of Responsibility
  locator_registry     ILocatorFactory     Registry / Factory Method: LocatorSpec -> locator

Choosing the tab
  url_specs            IUrlSpecification   Specification + Composite: https, host suffix
  tab_selector         ITabSelector        first tab the specification accepts

Clicking
  clickers             IClickAction        Command, Null Object (dry run), Decorator (retry)
  parts_factory        IClickPartsFactory  Abstract Factory: selector + locator + clicker

Attaching to a running Chrome
  selenium_driver_factory   IDriverFactory          the only Selenium import
  headless_manager_factory  IBrowserManagerFactory  the only selenium-headless import
  detach_policies           IDetachPolicy           let go without closing the window
  managed_session           IBrowserSession         Adapter over WebBrowserManager
  session_attacher          IBrowserAttacher        assembles the three above

Is it there?
  json_fetcher         IJsonFetcher
  endpoint_probe       IEndpointProbe
  readiness            IReadinessWaiter
  system_clock         IClock

Starting the debuggable Chrome
  chrome_command       IChromeCommandBuilder   immutable Builder
  profile_dir          IProfileDirResolver
  process_spawner      IProcessSpawner
  chrome_launch        IChromeLauncher

Wording
  reports              IClickReporter, IDebugOpenReporter

Deliberately NOT behind an interface: the pydantic values in `models` (a
DebugEndpoint is data, and abstracting data is the over-engineering the
house DIP rules forbid).
"""
