# ask_sonnet

"Toyota, ask Sonnet about ..." routes here. This specialist runs
`/home/adamsl/rol_finances/adws/adw_ask_sonnet.py` and relays the one line it
writes. Sonnet is the everyday model -- quick, for what we were working on or where something is; it answers read-only from the repos, recent commits,
the newest ADW runs and the handoff note.

- `--follow-up` continues the last Ask Sonnet thread (same adw_id, same context).
- Every answer is a run in `just obs`; the full text is in that run's
  `context_handoff/answers.md`.
- This Sonnet is a workflow agent, not the Claude Code session at the terminal.
