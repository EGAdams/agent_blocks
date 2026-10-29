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

If saving fails, the captured events stay in the local database; keep the
Save As prompt open. Background clicks from older extension versions are
recovered when saving with the updated recorder service. After updating the
extension files, reload the extension and refresh the website before starting
your next recording.

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

After a failed run, **View History** shows the failing step. To resume there,
leave Chrome on the page where it stopped and add `--from-step N` to the
command above, replacing `N` with that step number. Playback ignores matches
with zero width or height and keeps waiting or tries the recorded fallbacks.

## Downloading statements

Windows **Save As** is outside the website: its filename field and buttons
cannot be recorded. Record the website's **Download** click, then finish
the dialog manually if it appears while recording. Save the recording as usual.

Playback configures Chrome to save website downloads directly, without that
dialog, to `%USERPROFILE%\Downloads\BrowserRecorder`. The result prints the
download folder. Existing recordings already containing the website's download
click can be retried; no extension reload is needed. Cancel an already open
Save As dialog before retrying. Opening a PDF preview or choosing **Print / Save
as PDF** is a different operation; record the website's download link instead.

The folder can be set in the local, gitignored file
`adws/adw_data/browser_workflows/download_directory.txt` (one absolute path),
or overridden with `BROWSER_WORKFLOW_DOWNLOAD_DIR`. For Windows Chrome, use
a Windows path such as `C:\Users\rbarn\Downloads\BrowserRecorder`, even
when the menu runs in WSL. A local Linux Chrome defaults to
`~/Downloads/BrowserRecorder`. Chrome's download setting remains in effect in
the debug browser until Chrome resets it or is restarted.

Check the resulting file in that folder. A completed playback means its
recorded steps succeeded; it does not yet verify the downloaded PDF's contents
or wait for a download to finish.

Agents can request `browser recorder: list playbacks` or
`browser recorder: run My workflow` through the post office. Agent runs block
at steps needing approval or a secret; use the interactive menu for those.

Recordings and run evidence stay in the gitignored
`adws/adw_data/browser_workflows/` directory. See [DESIGN.md](DESIGN.md)
for the architecture and ADWs.
