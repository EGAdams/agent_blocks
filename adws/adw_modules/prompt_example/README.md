# First real ADW in the debug menu

From the playground root:

```bash
cd /home/adamsl/agent_blocks
uv run adws/adw_debug_console.py
```

Or from `agent_blocks/adws`, run `uv run adw_debug_console.py`.
Choose **10. Run Prompt ADW (2 phases)** and press Enter for a sample question,
or enter your own question. The answer and run's log path appear before the
menu returns. This invokes the existing `adws/adw_prompt.py`, which was already
an exact copy of `/home/adamsl/rol_finances/adws/adw_prompt.py` when added.

- **ADW:** the workflow definition, `adw_prompt.py`.
- **Phase:** a named step. `request` records your question; `prompt` calls the
  `learner` agent and validates its JSON response.
- **Run:** one execution of that ADW. Choosing option 10 twice creates two
  different `adw_id` values, each with its own two phases and trace.

The learner uses the repo's existing Pi/Claude subscription setup, with no
tools or extensions. Its roster and prompts live beside this README in
`config.yaml`, `system.md`, and `user.md`.

Inspect the printed run ID from the playground root:

```bash
just phases <adw_id>
just tail <adw_id>
```

Run traces are under `adws/adw_data/sessions/<adw_id>/events.jsonl`; the typed
answer is in `learner/envelope.json` in that session directory. The menu's
file-demo actions, including **Show recent logs**, use the console's displayed
session ID. Option 10 prints the separate Prompt ADW's session ID and log path.

Standalone execution uses the same ADW and learning configuration:

```bash
uv run adws/adw_prompt.py "Explain phases using a cooking example" \
  --agent learner --config adws/adw_modules/prompt_example/config.yaml
```
