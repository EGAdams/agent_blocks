# Voice check-in with the finance team

Toyota's existing voice chat now has a status route: a question about current finance-team progress can delegate to `progress_check` rather than launching a new database-backed analysis. The new role reads seven existing deliverables (four mapper JSON files, synthesis HTML, and the two receipt/expense reports), reports presence and headline figures from the files themselves, compares mapper timestamps for missing or stale results, and writes `reports/finance_team_progress_report.md`. It has only `Read` and `Glob` tools; the role prompt prohibits database and shell access. A normal finance-team orchestration run does not include this check.

## Where it lives

- `lancedb_memory/finance_team/roles.py` registers the `progress_check` role and defines its report and one-sentence response.
- `lancedb_memory/receptionist_team/roles.py` adds blurbs for `progress_check` and `expense_reconciler` and tells Toyota to distinguish progress/status questions from explicit requests to run analysis agents.
- `lancedb_memory/README.md` documents the voice prerequisites, commands, report, and status-versus-analysis examples.
- `specs/db33e572_finance-progress-voice-channel.md` records the implementation plan and static/manual verification steps. The existing voice transport was not changed in this diff.
- `.claude/skills/sssf/templates/sssf.config.yaml` also changes the default and planner models to `openai/gpt-5.6-sol`; this is separate from the voice/status flow.

## Use and verify

From `lancedb_memory`, with `dashboard-server.service` running on `localhost:8765`, `ANTHROPIC_API_KEY` and `ANTHROPIC_BASE_URL` unset, `parecord` available for the mic, and `ffplay`, `mpg123`, or `paplay` available for playback:

```bash
.venv/bin/python -m receptionist_team.main --voice --session toyota
```

Ask “what's the finance team's progress so far?” for a report-based check; “run the card mapper” requests a fresh analysis instead. Reuse `--session toyota` after reopening to recall earlier conversation and route results. To request only the status role directly, run `.venv/bin/python -m finance_team.orchestrate --only progress_check` from the same directory. Its answer depends on which of the seven existing reports are present; missing ones are reported, not regenerated.

For a wiring check from `lancedb_memory`, compile the two role modules, inspect the new role's tools and Toyota's blurb, and check the `--only` choices:

```bash
.venv/bin/python -m py_compile finance_team/roles.py receptionist_team/roles.py
.venv/bin/python -c "from finance_team.roles import ROLES; print(ROLES['progress_check'].allowed_tools)"
.venv/bin/python -c "from receptionist_team.roles import ROLE_BLURBS; assert 'progress_check' in ROLE_BLURBS"
.venv/bin/python -m finance_team.orchestrate --help
```

A spoken end-to-end check additionally requires live audio and the dashboard service; these static checks do not demonstrate a successful voice call or generated report.
