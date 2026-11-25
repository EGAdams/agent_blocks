# Failure Triage — design (designed and built 2026-11-25; see "As built" at the end)

Every failed task is already **detected** today. Nothing turns those detections into fixed problems. This
ADW closes that gap. It is report-only: it **never resends a task, never edits code, never deletes a failed
file**. Fixes still go through the existing paths (a build or plan by the owning agent, the tests, `adw_deploy`).

## What exists (inspected 2026-11-25 on SQMC)

| Piece | What it does | The gap |
| --- | --- | --- |
| ADW supervisor (`~/.cache/agent_bridge/adw_supervisor_state.json`) | Raises an incident for each failed Bridge task and shows it on the dashboard | Each alert is acknowledged and then forgotten |
| Post office alerts (`alerts/<agent>/`) | Tell the sender that a task failed | Same |
| `FailureForwarder` → mechanic `diagnose` | Every `task.failed` gets a diagnosis and a case in `mechanic_cases.jsonl` (84 cases). The diagnosis already says "Seen before: case-…, case-…" | Nobody reads "seen before". `ICaseHistory.record(..., correction)` exists, but **every `correction` is empty**, so the learning half was never connected |
| Machine Supervisor | Repair ladder, plus mail to EG | Only watches health checks in `machines.json`, not task failures |
| Scala → Yolanda (`scala/post_office_mail.py`) | Sends EG mail as a post-office `message` to `yolanda` | Reusable as is |

At design time, the 84 failed requests in `mailboxes/*/inbox/failed/` collapse into about 13 distinct errors:

| Count | Agent | Error (shortened) | Likely class |
| --- | --- | --- | --- |
| 29 | machine-ops | refused: strict JSON … validation error | code, **fixed in 7fa5717**, awaiting a clean run |
| 25 | claude-sonnet | `error_max_turns: no answer` | config: turn budget, or the prompt |
| 7 + 7 | codex, codex-writer | `stream disconnected before completion` | environment: network or provider |
| 4 | opus | `400 … Claude Code 2.1.250 does …` (CLI version) | environment: CLI upgrade |
| 3 + 1 + 1 | claude-sonnet | session limit, OAuth 401, login expired | environment: account |
| 2 | codex | `gpt-6-luna` model metadata not found | config |
| 2 | codex-writer | answer withheld: contained credentials | guard working, but check the prompt |
| 2 | claude-sonnet | did not finish within 900 s | config or the task |
| 1 | codex | workdir outside agent_blocks | caller sent a bad request |
| 1 | mechanic | suites not green after fix attempts | needs a person |

## The workflow

`adws/adw_failure_triage.py` runs one triage pass. It is a deterministic ADW with no model in its path,
started by a systemd user timer on SQMC every 15 minutes. It can also be run by hand, with
`--dry-run` to print the decisions without sending anything.

| Phase | Responsibility | Input | Output | Failure/recovery |
| --- | --- | --- | --- | --- |
| `collect` | Read failures from every source since the last cursor | `IFailureSource`s | `list[Failure]` | A source that can't be read is reported in the run record. The pass continues with the others, and that source's cursor stays put |
| `fingerprint` | Reduce each failure to a stable key | `Failure` | `Failure` + `fingerprint` | Pure function, no I/O |
| `reconcile` | Merge into the cluster ledger: new, grown, regressed, quiet | ledger + failures | `list[ClusterChange]` | The ledger is written atomically (tmp + rename). A crash re-reads the same failures, and the merge is idempotent by task id |
| `route` | For each cluster that needs an owner and has none, send **one** post-office request to the owner | changes + `owners.json` | routed task ids | A refusal (unknown agent, not on the card) is recorded on the cluster and escalates to EG |
| `notify` | Mail EG about repeats, regressions and EG-only classes | changes | mail task ids | Limited to one mail per cluster per day and one digest per pass. If mail fails, the change stays `pending_mail` and is retried next pass |
| `report` | One line, plus `--json-out` | everything | `DONE: 3 open clusters, 1 routed, 1 mailed` | — |

### Sources (`IFailureSource`, one adapter each)

1. `MechanicCaseSource` reads `mechanic_cases.jsonl`. This is the main source, because every post-office
   failure lands there through the `FailureForwarder`. Bridge tasks to post-office agents arrive the same
   way (`context.bridge_task_id` links them).
2. `BridgeSupervisorSource` reads the incidents in `adw_supervisor_state.json` for Bridge tasks that never
   reached the post office, such as agents run directly by the worker (deploy, notes, scala-storage).
   It skips a task whose id is already seen through source 1.

Later sources (BATI and R46 workers, failed `adw_deploy` runs) are one adapter each. The phases stay as they are.

### Fingerprint (pure logic, the part most worth testing)

`fingerprint = sha1( agent + "|" + normalise( error ))[:12]`. `normalise`:
- lowercases the error;
- replaces task, case, ADW and Bridge ids, hex runs, numbers, ISO and clock times, and absolute paths with
  `<id>`, `<n>`, `<t>` and `<path>`;
- cuts at the first `Reason:` detail line, or at 160 characters, whichever comes first.

So "resets 10:20pm" and "resets 9:05am" fall into one cluster. If a normaliser rule is wrong, the cost is
two clusters that should be one. That is noisy but never loses anything, and the rules are data
(`normalise_rules.json`) with a test per rule.

### The ledger (`adws/adw_data/failure_triage/clusters.json`, gitignored)

The ledger is a `Cluster` (Pydantic) per fingerprint. It holds agent, sample error, `first_seen`,
`last_seen`, `count`, task ids (the last 20), `klass`, `owner`, `state`, `routed_task`, `fix` (a commit or
note) and `mailed_at`.

```
open ──route──▶ routed ──EG/owner records a fix──▶ fixed ──quiet_days pass, or one clean run──▶ closed
  ▲                                                  │
  └──────────── same fingerprint fails again ◀───────┘   (= "regressed": mail EG at once)
```

- A fix is recorded by `adw_failure_triage_fix.py --cluster <id> --fix "7fa5717 …"`. This is a separate ADW,
  run by EG, by me, or by the owner agent at the end of its build. Recording a fix also calls
  `ICaseHistory.record(…, correction=fix)` for that error. That fills the empty `correction` field, so the
  mechanic's next "Seen before" names the fix as well as the earlier cases. **This is the learning step.**
- `closed` needs evidence: either a success from the same agent and operation after the fix, or
  `quiet_days` (default 3) with no new occurrence. Triage never creates that success itself, because it
  never resends.

### "Zero" means zero open clusters

The `failed/` files are history. They are never moved or deleted. The target is that no cluster is
`open`, `routed`, or `fixed` but unconfirmed, and the dashboard's Worker Activity tab can show that count.

## Owners (`adws/failure_triage/owners.json`, data, not code)

Rules match first-to-last on `agent` and an error regex, and give a class, an owner and a task type.

| Class | Example | Owner | What gets sent |
| --- | --- | --- | --- |
| code | machine-ops validation | `sonnet`, `plan` | The error, the request text, 3 task ids and the mechanic's diagnosis, asking for the cause and a proposed patch with a test. It is not sent as a `build`: a build is non-idempotent, so EG approves it after reading the plan |
| config | max_turns, 900 s timeout, unknown model | `sonnet`, `plan` | The same, pointing at the agent card or config file |
| environment / account | session limit, OAuth expired, CLI version, stream disconnect | **EG only**: mail, no agent | No agent can renew a login or upgrade a CLI under the house rules. The mail says what to do |
| caller error | bad workdir, credential withheld | the **sender**'s alert board | Shows the request was wrong. Nothing is broken |
| needs a person | mechanic refused | EG mail | — |
| unmatched | anything new | `mechanic`, `diagnose` with a "triage" note, plus a digest line to EG | Unknown errors never route themselves to a code-writing agent |

## Mail to EG

Mail goes as a post-office `message` to `yolanda`, as Scala does it, from sender identity `failure-triage`
(a new `agents.json` entry with no runtime, like `post-office`). It is sent when:
- a cluster reaches `count ≥ 2` for the first time ("this keeps happening");
- a fixed cluster fails again ("regressed");
- a cluster is EG-only.

Each pass sends at most one digest. Subject: `[Triage] 2 repeating failures, 1 regression`. Each cluster
gets 3 lines: what, how often, and who has it now or what EG should do. The body holds no prompt text
beyond the first 200 characters, and no tokens, because errors are run through the post office's
existing credential check first.

## Interfaces (`adws/failure_triage/interfaces.py`)

`IFailureSource.read(since) -> list[Failure]`, `IFingerprinter`, `IClusterLedger.load/save`,
`IOwnerTable.match(failure) -> OwnerRule`, `ITriageRouter.send(cluster, rule) -> str`, which wraps the
post-office `create_message` + `send_message` operations, and `INotifier.digest(changes)`.
`failure_triage/kit.py` is the only composition root. Models (`Failure`, `Cluster`, `OwnerRule`,
`ClusterChange`) are Pydantic.

## Safety rules, enforced in code

1. The router can only call `send_message` with `task_type` in `{plan, diagnose, message}`. `build`,
   `implement` and `repair` raise.
2. One routed task per cluster. Re-routing needs `--reroute <cluster>`.
3. Nothing under `post_office/mailboxes` is opened for writing. Sources are read-only.
4. The pass runs no tests and starts no units.

## Tests

- Pure logic: one fingerprint per real error string above, plus the state transitions (open → routed →
  fixed → closed and regressed) and owner-rule matching.
- Orchestration: one pass over a fake post-office root built from copies of real envelopes, using
  `--dry-run` and then a recording router and notifier, plus one real ADW run in the style of `test_operations`.
- Offline throughout: no model, no mail, no Bridge.

## Rollout, and getting to zero

1. Build it and run `--dry-run` against SQMC's real data. Check that the about 13 clusters above come out as 13.
2. Record the fix that already exists (`machine-ops` → 7fa5717) and have ChatGPT resend one `run_test`.
   That moves 29 failures to `closed`.
3. Turn the timer on. Work through the rest by class: EG's account and CLI items first (mail, no code),
   then the `max_turns` cluster (25, the largest open one) as a Sonnet `plan`.

## Decisions (EG accepted the defaults, 2026-11-25)

- Code and config clusters go to `sonnet` as a `plan`. EG is mailed at 2 occurrences, and `quiet_days` is 3.
- ChatGPT sees the triage as Bridge agent `failure-triage` (see below).

## As built (2026-11-25) — where it differs from the design above

- **One source, not two.** `FailedMailboxSource` (`sources.py`) reads every `mailboxes/*/inbox/failed/*.json`
  envelope directly. That covers Bridge tasks to post-office agents too, so the mechanic-case and supervisor
  sources were not needed. Re-reading every envelope on each pass is idempotent by task id, so there is no cursor.
- **Fingerprint and owner are one rule table.** `normalise_rules.json` blurs ids, paths and numbers.
  `owners.json` holds named rules, each with a class, an owner, a task type and advice for EG. Every one of
  the 85 real errors in `tests/real_errors.jsonl` matches a named rule; a test asserts this.
- **Closing.** A cluster with no new failure for `quiet_days` closes, whatever its state. So history older
  than three days closes on the first pass, and "zero" is about what is still happening. A fixed cluster
  that fails again reopens as `regressed` and is mailed.
- **Learning loop.** Recording a fix (`adw_failure_triage_fix.py`) writes a correction case to
  `mechanic_cases.jsonl`. `JsonlCaseHistory.similar()` lists a corrected case first, as "(fixed by: ...)",
  so the mechanic's next diagnosis of the same error names the fix.
- **ADWs.** `adw_failure_triage.py` is the pass (five phases: collect, reconcile, route, notify, report;
  fingerprinting happens inside reconcile). `adw_failure_triage_status.py` runs `status|clusters|cluster`,
  and `adw_failure_triage_fix.py` runs `fix|reroute`. `kit.py` is the only composition root.
- **Schedule.** `systemd/failure-triage.{service,timer}` is a user timer on SQMC, every 15 minutes.
- **Bridge agent `failure-triage`** (protected; worker flag `~/.config/failure_triage/bridge_enabled`):
  `lancedb_memory/agent_bridge_worker/triage_agent.py` over `adapters/triage_process.py`. `status`, `clusters`
  and `cluster` run as `inspect`; `fix` and `reroute` run as `implement` and change only the ledger. Values are
  checked before the ADW is run: the state is one of four words, the cluster is 4–12 hex digits, and a fix is
  5–400 characters of one collapsed line.
- Tests: `cd adws && uv run --quiet --with pydantic --with pytest pytest -q failure_triage/tests`, and
  `cd lancedb_memory && .venv/bin/python -m agent_bridge_worker.tests.test_triage_agent`.
