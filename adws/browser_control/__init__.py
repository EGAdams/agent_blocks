"""Browser Control: ten traceable browser operations (browser.open ... browser.close).

Everything here is reuse. The Windows debug browser is started by browser_session's
DebugChromeLauncher; the page is driven by cdp_browser's CdpWorkflowBrowser; the
words that make a click consequential come from browser_workflows' classifier.
This package adds only the operations, the controllable-browser table, and the
ControlPlane that holds the injected parts. Each operation is an ADW phase, so
the SSSF lifecycle log and the object trace (Object Debugger) record it.
"""
