# Browser recorder: getting started

Run the finance menu from WSL:

```bash
cd /home/adamsl/agent_blocks
lancedb_memory/.venv/bin/python /home/adamsl/rol_finances/menu_code/run_menu.py
```

Select **Browser Automation Workspace**.

## Set up once

1. Select **Open debug Chrome (for recording and playback)** and enter your
   starting page's URL. Use the Chrome window this opens for recording and playback.
2. Select **Install or update the recorder extension**. Copy the Windows folder
   printed in its result.
3. In that debug Chrome, open `chrome://extensions`, turn on **Developer mode**,
   select **Load unpacked**, and choose the printed folder (normally
   `%LOCALAPPDATA%\adw-browser-recorder`). After updates, use **Reload** instead.

This Chrome uses its own profile; sign in there if your workflow needs it.

## Record and replay

1. Return to the menu and select **Record / replay / edit playbacks**.
2. Select the website tab in debug Chrome, then select **Start Recording** in
   the menu. This also starts the recorder service. If it says the extension
   is not connected, wait a few seconds and retry.
3. Perform your workflow in Chrome.
4. Select **Stop Recording**, then give it a name at **Save As?**.
5. Select **Choose Playback** and pick that name to run it again.

Use **Edit Saved Playback** to inspect or change steps and **View History**
to inspect results. Playback asks for secrets and approval when needed;
passwords are not saved. Other recorded field values are stored locally.
Begin with a simple navigation/search workflow to learn the controls.

The workspace can also run directly:

```bash
cd /home/adamsl/agent_blocks
uv run --quiet adws/browser_workspace_menu.py
```

To replay a saved workflow directly:

```bash
uv run --quiet adws/adw_browser_execute_saved_playback.py --name "My workflow" --interactive
```

Agents can request `browser recorder: list playbacks` or
`browser recorder: run My workflow` through the post office. Agent runs block
at steps needing approval or a secret; use the interactive menu for those.

Recordings and run evidence stay in the gitignored
`adws/adw_data/browser_workflows/` directory. See [DESIGN.md](DESIGN.md)
for the architecture and ADWs.
