# Message for ChatGPT

Use Star Trek Bridge directly for future checks and development. Start with
`list_available_agents`; the live agent list is authoritative.

For service checks, submit exactly once:

- agent: `machine-ops`
- workspace: `machine_ops`
- operation: `inspect`
- prompt: `{"operation":"status","unit":"adw-supervisor.service"}`

Poll that same task ID until terminal, then retrieve the full result. For a
real named test, use:
`{"operation":"run_test","unit":"agent-bridge-worker.service","suite":"post-office-operations"}`.
Other operations are `journal_tail` (optional `lines`, 1–200), and `check`.
Approved units: dashboard-server, adw-supervisor, agent-bridge-worker,
agent-blocks-server, toyota-receptionist, each with `.service`.
Restarts, arbitrary commands, paths, Python and SQL are unavailable.

For lifecycle evidence, use `object-debugger`, workspace `object_trace`,
operation `inspect`, prompt `status`, `recent 5`, or `task t_<24 hex digits>`.
Toyota has her own authenticated read-only bridge identity and can address
`machine-ops@machine_ops: <JSON>` and
`object-debugger@object_trace: status` through her existing bridge desk.
Task ownership is per client: use Object Debugger to inspect another client's
evidence; don't expect Toyota's result endpoint to disclose ChatGPT-owned tasks.

For code changes, submit `implement` to `codex-writer` or `claude-sonnet`,
workspace `agent_blocks_dev`, with a bounded request and an idempotency key.
This edits an isolated development checkout and reports actual changes and
trusted test results. The live `agent_blocks` workspace stays read-only.
Review the returned diff and checks. Production deployment is a separate,
locally reviewed operation; a task prompt cannot authorize it or restart services.

Keep ADW Supervisor coverage marked incomplete until its actual remote queue
adapter exists. Configured models remain runtime-unverified until tested.
Never replay failed task `t_cb29dcb55654efa68f29fee9`, and never submit duplicates
because a task remains queued. Ask for status and results using its original ID.

Activation evidence, task IDs and any remaining limitations are recorded in
`agent_bridge/docs/BOOTSTRAP_VERIFICATION.md`.
