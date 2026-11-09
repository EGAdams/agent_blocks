# bridge_browser

Toyota's Groq specialist for the Bridge agent `browser`. The worker side is
`agent_bridge_worker/browser_agent.py`; each task becomes one run of the Browser Control ADW
(`adws/adw_browser_control.py`), with its own ADW id, lifecycle log and object trace.

This is the **same** Bridge agent, worker adapter and ADW that ChatGPT drives over MCP. Toyota gains no
operation of her own, and there is no second browser automation path -- only a second caller. See
`agent_bridge/docs/BROWSER_CONTROL.md` for the environment, the failure causes and what has been verified
end to end, and the `browser-form-control` skill for the agent-facing loop.

## The loop

`form_controls` first, always: it lists every field, dropdown (with its options and the one now chosen) and
button, each with the `css` to pass to the next step. A guessed selector is the commonest reason a form step
fails, so a plan that is handed something that isn't a selector asks for a `form_controls` run instead of
guessing. Then `select_option` for the dropdowns, `fill` for the fields, `click` the page's own submit
control, and `read` to see what actually came back. A submitted form is not a result.

## What code decides, never the model

- The operation's lane: a read is `inspect`, a change is `implement`, so a change can never be sent as a read.
- `confirm` is **never** set from here. A click whose words look consequential ("pay", "submit", "delete") is
  refused by the ADW, and that refusal is reported: EG's spoken yes to "shall I send it" is a yes to this
  request, not standing authorization to press a button that moves money. He presses that one himself.
- A password, PIN, one-time code, CVV or anything else matching the credential pattern is refused before it is
  sent, on top of the ADW refusing a field it marks `secret`.

Every `implement` operation is read back and waits for a spoken yes, the same gate `bridge_writer` and
`bridge_scanner` use. That is deliberate and was not relaxed for the form loop.

See `receptionist_team/bridge_client/README.md` (Bridge specialists) and `browser_plans.py`.

## Reading what a download saved

`download` keeps a file; `downloaded_files` and `view_file` read it back. Those two are **not** Bridge tasks:
`BrowserBuilder.direct()` answers them by calling the gateway's MCP tools `browser_downloaded_files` and
`browser_view_file` with Toyota's own credential (`bridge_client/files.py`, `McpBridgeFiles`), so there is no
submit, no wait and no yes -- nothing changes. The gateway shows a client only the files its own tasks
downloaded, so Toyota sees what *she* downloaded, never ChatGPT's.

She gets the facts and the text, never the page image: her conversation model reads text only. A view asks for
the `preview` rendition and the transport for this reader allows 2 MB, because the gateway always attaches one
page image. The words she repeats are clipped to fit one spoken reply and say where to continue ("ask for more
of file f_... from character 380"), and they are introduced as the website's words, not instructions. A file
that has only page images (a scanned plat) is described, and she says ChatGPT can look at the images.
