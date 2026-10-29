"""Chrome DevTools Protocol, spoken directly: no Selenium, no chromedriver,
no Playwright, no screenshots.

`CdpWorkflowBrowser` is one more `browser_workflows.interfaces.browser.
IBrowserAdapter`, so playback, the statement templates and Cashy all run on
it unchanged. It reads the DOM with Runtime.evaluate, finds elements as
remote objects, and clicks with Input.dispatchMouseEvent at the element's
own box (the box the DOM reports, never a point found in a picture).

  interfaces     ICdpChannel, ICdpConnector, ITargetDirectory, ITargetChooser
  websocket      StdlibWebSocket          a minimal RFC 6455 client (stdlib only)
  channel        WebSocketCdpChannel      ICdpChannel: one target's command stream
  targets        HttpTargetDirectory      ITargetDirectory over /json/list, /json/new
                 LoopbackOnly             refuses any debug address off this machine
  choosers       FirstPageChooser, PreferredTargetChooser    ITargetChooser
  element        CdpElement               IWorkflowElement over a remote object
  browser        CdpWorkflowBrowser       IBrowserAdapter (+ go_back, open_tab, target_id)
  provider       CdpBrowserSessionProvider   IBrowserSessionProvider

Releasing closes the sockets. Nothing here ever closes a tab or the browser:
the window is the user's.
"""
