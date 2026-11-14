# Debug pilot

`adws/adw_debug_pilot.py` is the test ADW the dashboard's Live ADW Sequence
Debugger follows. It does nothing useful on purpose: five small phases that
record their own lifecycle, a tool call, a wait and two cross-phase messages,
through the ordinary SSSF session, executor and tracer.

The whole feature, including every pattern used here, is described in
`letta-code/dashboard/adw_debugger_ui/DESIGN.md`. What is specific to this side:

| File | One job |
|---|---|
| `interfaces.py` | The six interfaces: `IDebugEvents`, `IPhaseProbe`, `IPilotStep`, `IFaultInjector`, `ISleeper`, and the `FaultPlan` model |
| `events.py` | Adapter: one debugger event becomes one SSSF `EventRecord`, details under `payload["adw_debug"]` |
| `monitored_phase.py` | Decorator: a step run as an SSSF phase that announces `constructed`, `purpose`, `bound` and `cleanup` |
| `steps.py` | The work. Plain code, no SSSF, callable with a fake probe |
| `faults.py` | Strategy: `NoFaults`, or `PlannedFaults` when a test-only flag is given |

Rules:

- Cleanup runs from a `finally` and from `ExitStack`, never from `__del__`.
- A cleanup event never changes a phase's outcome; SSSF's `phase_end` does.
- `--kill-at` exits with `os._exit`, so no cleanup and no `phase_end` are
  written. That is the point: the trace must show the gap, not hide it.
- Phases run one at a time. An SSSF `Run` has one sqlite connection and one
  console lane, so this pilot cannot produce overlapping phases.
- Fault flags are for tests. Nothing else reads `FaultPlan`.

Run: `cd ~/agent_blocks && lancedb_memory/.venv/bin/python adws/adw_debug_pilot.py --pace 1`

Tests: `cd adws && uv run --quiet --with pydantic --with pytest --with pyyaml --with rich --with python-dotenv pytest -q debug_pilot/tests`

## Production phase monitoring

Scala's `adw_interface_scala_file_scan.py` now constructs all seven existing
phases, then wraps them with `adw_modules.monitored_phase.MonitoredSssfPhase`.
The decorator preserves params, the original handle, return values (including
skips) and exceptions. Waiting instances and purposes are real construction
events; SSSF still supplies phase outcomes. Cleanup describes the monitor's
scope only. ExitStack closes unreached monitors if execution stops early.
The shared event sink is `adw_modules.debug_events`; `debug_pilot.events`
keeps compatibility imports for the original pilot.
