# Playground training viewer

Copied from `/home/adamsl/rol_finances/adws/visualizer` at finance revision
`f0315dcbe682fd0b0f6179259be9e9c01f04b1f2`. This is the finance viewer with its
expandable phase sections, complete event terminal, and browser report links.
The former viewer under `.claude/skills/sssf/apps/visualizer` is preserved.

From the playground root:

```bash
cd /home/adamsl/agent_blocks
just obs
```

Open **http://localhost:4613**. Select a run, click a phase block, and expand
its detail sections. In the event terminal, **Open remaining work report**
opens the saved HTML over HTTP. Receipts and statements in that report are
served through the same server.

After fixing an expense, click **Refresh Totals** beside **Expense summary**
in the report. It starts the existing tax-status ADW, reads current records,
and opens a new report with the updated list and totals. It uses five code
phases and no AI calls. Original snapshots stay available. On a failed check,
the displayed totals stay unchanged and you can retry. An expense remains
listed when its guided review still needs confirmation.

For the first training tax-status run:

- Trace: http://localhost:4613/#/49228daa
- Report: http://localhost:4613/api/sessions/49228daa/reports/remaining-work.html

The server reads `agent_blocks/adws/adw_data/sssf.db`; session reports come from
the neighboring `sessions/<adw_id>/` directory. Reports are served in place,
so each future run is available without copying its HTML elsewhere.

Finance evidence and relative source-document paths resolve against the sibling
`rol_finances` checkout. Set `ROL_FINANCE_ROOT` when that checkout is elsewhere.
`SSSF_DB` selects another trace database; `PLAYGROUND_VIEWER_PORT` changes the
port used by `just obs`. The finance viewer on ports 4600/4601 is independent.

`just obs` builds the copied viewer and serves both the UI and API from one
process. For frontend development, keep that server running and run
`bun run dev` here for Vite on port 4614, proxying its API to 4613. Set `PORT`
and `UI_PORT` if using other ports.

Checks:

```bash
cd /home/adamsl/agent_blocks/adws/visualizer
bun install --frozen-lockfile
bun test
bun run build
```
