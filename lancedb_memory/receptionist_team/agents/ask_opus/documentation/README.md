# ask_opus

"Toyota, ask Opus about ..." routes here. This specialist runs
`/home/adamsl/rol_finances/adws/adw_ask_opus.py` and relays the one line it
writes. Opus is the boss model -- deeper and slower, for a bug or a hard question; it answers read-only from the repos, recent commits,
the newest ADW runs and the handoff note.

- `--follow-up` continues the last Ask Opus thread (same adw_id, same context).
- Every answer is a run in `just obs`; the full text is in that run's
  `context_handoff/answers.md`.
- This Opus is a workflow agent, not the Claude Code session at the terminal.
