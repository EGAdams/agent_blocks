# `runner.py`

The `Run` object: config + `adw_id` + agent map + tracer + console, bound
once per ADW invocation. `run.phase(PhaseParams(...))` is the one phase
primitive — a context manager used for all three phase kinds (`engineer`,
`agent`, `code`). Success must be earned: every phase defaults to `fail`;
only a clean exit flips it to `success` (agent phases additionally require
a parsed envelope + green gates, enforced inside `PhaseHandle.call`, which
delegates to `agents.execute`).

## Classes

- **`PhaseHandle`** — the object yielded by `Run.phase()`. Scopes `log()`
  and `call()` to the phase that is currently open.
- **`Run`** — one per `adw_id`. Owns the tracer, console, session
  directories, agent map, and running token/cost totals.

---

## `PhaseHandle.__init__(self, run, phase)`

Stores a back-reference to the owning `Run` and the `Phase` record it is
scoped to. No side effects beyond attribute assignment.

```mermaid
sequenceDiagram
    participant Caller as Run.phase (contextmanager)
    participant PH as PhaseHandle

    Caller->>PH: __init__(run, phase)
    PH->>PH: self.run = run
    PH->>PH: self.phase = phase
```

---

## `PhaseHandle.log(self, **payload)`

Records a structured log event against the current phase, mirrors it to
the console, and — only for `engineer`-kind phases carrying an `input`
key — also records it as a session request on the tracer.

```mermaid
sequenceDiagram
    participant Caller
    participant PH as PhaseHandle
    participant Tracer
    participant Console

    Caller->>PH: log(**payload)
    PH->>Tracer: event(EventRecord(adw_id, phase_id, "log", name, payload))
    PH->>Console: note("k: v, ...")
    alt phase.kind == "engineer" and "input" in payload
        PH->>Tracer: session_request(adw_id, str(payload["input"]))
    end
```

---

## `PhaseHandle.call(self, call: AgentCall) -> EnvelopeBase`

Guards that the current phase is an `agent` phase, then hands off to
`agents.execute` to actually run the agent call and return its parsed
envelope.

```mermaid
sequenceDiagram
    participant Caller
    participant PH as PhaseHandle
    participant Agents as agents.execute

    Caller->>PH: call(AgentCall)
    alt phase.kind != "agent"
        PH-->>Caller: raise RuntimeError
    else
        PH->>Agents: execute(run, phase, call)
        Agents-->>PH: EnvelopeBase
        PH-->>Caller: EnvelopeBase
    end
```

---

## `Run.__init__(self, cfg, adw_id, tracer, engineer)`

Builds the run's fixed state: config, id, tracer, a fresh `Console`, the
engineer name, an empty phase list, zeroed token/cost counters, the next
phase sequence number (continuing a joined run via
`tracer.max_phase_seq`), the repo root, the session/context-handoff
directories (created if missing), and the on-disk agent map — loaded from
`agent_map.json` if a previous run already wrote one.

```mermaid
sequenceDiagram
    participant Caller
    participant Run
    participant Tracer
    participant GitHelper as git_helper
    participant FS as filesystem

    Caller->>Run: __init__(cfg, adw_id, tracer, engineer)
    Run->>Run: self.console = Console(tracer, adw_id)
    Run->>Tracer: max_phase_seq(adw_id)
    Tracer-->>Run: last sequence number
    Run->>GitHelper: repo_root()
    GitHelper-->>Run: repo_root path
    Run->>FS: ensure_dir(data_dir/sessions/adw_id)
    Run->>FS: ensure_dir(session_dir/context_handoff)
    Run->>FS: agent_map.json exists?
    alt agent_map.json exists
        FS-->>Run: file contents
        Run->>Run: agent_map = json.loads(contents)
    else
        Run->>Run: agent_map = {}
    end
```

---

## `Run.save_agent_map(self, agent, entry)`

Updates the in-memory agent map and persists the whole map to
`agent_map.json`.

```mermaid
sequenceDiagram
    participant Caller
    participant Run
    participant FS as agent_map.json

    Caller->>Run: save_agent_map(agent, entry)
    Run->>Run: agent_map[agent] = entry
    Run->>FS: write_text(json.dumps(agent_map, indent=2))
```

---

## `Run.add_usage(self, tokens, cost)`

Accumulates token/cost totals on the `Run` and mirrors the increment into
the tracer's own sqlite-backed accumulation.

```mermaid
sequenceDiagram
    participant Caller
    participant Run
    participant Tracer

    Caller->>Run: add_usage(tokens, cost)
    Run->>Run: self.tokens += tokens
    Run->>Run: self.cost += cost
    Run->>Tracer: session_add_usage(adw_id, tokens, cost)
```

---

## `Run.phase(self, params: PhaseParams)` *(context manager)*

The one phase primitive. Allocates the next sequence number and a `Phase`
record defaulted to `status="running"`, registers it with the tracer and
console, then yields a `PhaseHandle` to the caller's `with` block.

- On a clean exit: marks the phase `success`, records `phase_end`, and
  upserts the final phase state.
- On any exception: marks the phase `fail` (success must be earned),
  records `error` + `phase_end`, upserts the phase, finishes the whole
  session as failed, tells the console, and re-raises — the caller always
  sees its own exception.

```mermaid
sequenceDiagram
    participant Caller
    participant Run
    participant Tracer
    participant Console
    participant PH as PhaseHandle

    Caller->>Run: with run.phase(params) as ph
    Run->>Run: seq += 1
    Run->>Run: build Phase with status running
    Run->>Run: phases.append(phase)
    Run->>Tracer: phase_upsert(phase)
    Run->>Tracer: event("phase_start")
    Run->>Console: phase_started(phase)
    Run->>PH: yield PhaseHandle(run, phase)
    Caller->>PH: (uses ph.log / ph.call inside the with-block)

    alt body raises
        Caller-->>Run: BaseException propagates
        Run->>Run: phase.status = "fail"
        Run->>Run: phase.error = str(error)[:1000]
        Run->>Tracer: event("error")
        Run->>Tracer: event("phase_end", status="fail")
        Run->>Tracer: phase_upsert(phase)
        Run->>Tracer: session_finish(adw_id, ok=False)
        Run->>Console: phase_ended(phase, elapsed)
        Run->>Console: session_finished(False, tokens, cost, db)
        Run-->>Caller: re-raise
    else body completes normally
        Run->>Run: phase.status = "success"
        Run->>Tracer: event("phase_end", status="success")
        Run->>Tracer: phase_upsert(phase)
        Run->>Console: phase_ended(phase, elapsed)
    end
```

---

## `Run.finish(self, accepted=True, reason="") -> int`

Finalizes the run exactly once, settling the db record, the console
banner, and the process exit code together so they can never disagree.
Two criteria must both hold for the run to count as `ok`: every phase
succeeded (`phases_ok`), **and** the caller's own acceptance test passed
(`accepted`) — a phase that ran the test suite did its job even if the
suite came back red, so the phase succeeds while the run must not.

```mermaid
sequenceDiagram
    participant Caller
    participant Run
    participant Tracer
    participant Console

    Caller->>Run: finish(accepted, reason)
    Run->>Run: phases_ok = phases and all(p.status == "success")
    Run->>Run: ok = phases_ok and accepted

    alt phases_ok and not accepted
        Run->>Tracer: event("error", name="not_accepted", reason)
        Run->>Console: note("not accepted: ...")
    end

    Run->>Tracer: session_finish(adw_id, ok=ok)
    Run->>Console: session_finished(ok, tokens, cost, db)
    Run-->>Caller: return 0 if ok else 1
```
