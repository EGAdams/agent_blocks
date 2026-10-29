# Browser Workflows — record once, replay from the menu, an ADW, or an agent

This subsystem sits beside `browser_session/` (the Fifth Third "Log In" click)
and reuses it rather than replacing it. Every behaviour lives behind an
`I`-prefixed Protocol in `interfaces/`; `kit.py` is the one module that names
concrete classes.

## What is reused, and what is new

| Need | Reused from | New here |
|---|---|---|
| Reach a Chrome the user already has open | `browser_session`: `SeleniumAttachedDriverFactory`, `SeleniumHeadlessManagerFactory` (selenium-headless's `WebBrowserManager`), `StopDriverServiceDetach` (never quit), `DevToolsEndpointProbe` | `SeleniumWorkflowBrowser`, an `IBrowserAdapter` with typing, selects, screenshots and page queries |
| Open that Chrome with a debug port | `adw_interface_browser_debug_open.py`, unchanged | — |
| One engine for many small ADWs | `agent_comms.operations.base`: `IOperation`, `OperationOutcome`, `OperationSequence` | `browser_workflow_adw.py`, the harness that `post_office_adw.py` is for the post office |
| Agents discovering and requesting work | `agent_comms`: `agents.json` cards, `KeywordRouter`, `ByTaskType`, `AdwSubprocessExecutor`, `FailureForwarder` to the mechanic | one card (`browser-recorder`) and one runtime builder |
| A console menu | `/home/adamsl/smart_menu`'s `Menu` loop (any item with `title` + `execute()`) | items built from SQLite every time the list opens |
| Recording what a person does | nothing existed | the MV3 extension in `chrome_extension/` and the recorder service |

## The Windows / WSL boundary

```
 Windows                                   WSL2 (networkingMode=mirrored)
 ───────────────────────────────           ─────────────────────────────────────
 Chrome (debug profile, port 9222)         recorder service  127.0.0.1:8781
   └─ Browser Recorder extension  ──ws──►    (aiohttp; Host + Origin guarded)
        service worker                        └─ SQLite click_workspace_sequences.db
   ▲                                                 ▲
   └──── CDP 127.0.0.1:9222 ◄── playback (Selenium via WebBrowserManager)
```

Mirrored networking makes 127.0.0.1 the same address on both sides (measured:
`curl.exe` on Windows got HTTP 200 from a WSL listener). So the extension
opens a WebSocket straight to the service; Native Messaging would need a
Windows-registered host executable and adds nothing here.

The service accepts a connection only when:
* `Host` is `127.0.0.1:<port>` or `localhost:<port>`, which defeats DNS rebinding;
* for the WebSocket, `Origin` is exactly `chrome-extension://<our id>`. The
  manifest's `key` pins the ID, so a web page can never pass this check;
* for the control API, no `Origin` at all (a CLI or ADW) and
  `Content-Type: application/json`, which a cross-site form cannot send
  without a preflight we never answer.

## Recording protocol (JSON over the WebSocket)

| direction | type | payload |
|---|---|---|
| ext → svc | `hello` | `extension_version`, `state` |
| svc → ext | `start_recording` | `session_id` |
| ext → svc | `recording_started` | `session_id`, `tab_id`, `url`, `title` |
| ext → svc | `event` | `session_id`, `event` (a `RawBrowserEvent`) |
| svc → ext | `stop_recording` | `session_id` |
| ext → svc | `recording_stopped` | `session_id`, `event_count` |
| ext → svc | `start_requested` | from the popup's Start button (the service then sends `start_recording`) |
| ext → svc | `stop_requested` | `session_id`, from the popup's Stop button |
| ext → svc | `save_requested` | `session_id`, `name`, from the popup's "Save As?" |
| ext → svc | `discard_requested` | from the popup's Discard button |
| svc → ext | `state` | `state`, `session_id`, `message` |
| both | `ping` / `pong` | keeps the MV3 service worker alive |

Raw events are what the page saw: `click`, `text_committed` (one per field,
on `change`, never per keystroke), `select`, `check`, `key` (Enter),
`navigation` (with Chrome's `transition_type`) and `tab_switch`. Secrets never
leave the page. A sensitive field's value is replaced by `null` and
`sensitive: true` in the content script, then checked again in Python against
the same `sensitivity_rules.json`.

## Normalising: raw events → reusable actions

`ActionNormalizer` is a pipeline of single-purpose rules (Strategy objects, in order):

1. `MapRawEvents`: each raw event becomes a `RecordedAction`.
2. `DropFieldFocusClicks`: a click into a field that is then typed into is noise.
3. `MergeTextEntries`: consecutive entries into one field keep only the last value.
4. `FoldFollowedNavigation`: a `link`/`form_submit` navigation right after a
   click becomes that click's `wait_after: url_equals`, not a separate step.
5. `RedactSecrets`: re-applies the sensitivity rules; the value is dropped
   and the step asks a person at playback.
6. `FlagConsequentialSteps`: Pay / Transfer / Delete / Submit-style
   clicks are marked `requires_approval`.
7. `EnsureStartsWithNavigation`: playback always begins on the recorded page.

## SQLite: `adws/adw_data/browser_workflows/click_workspace_sequences.db`

```mermaid
erDiagram
    recordings ||--o{ recording_steps : has
    recording_steps ||--o{ step_locators : "found by"
    recording_sessions ||--o{ captured_events : staged
    recording_sessions |o--o| recordings : "saved as"
    recordings ||--o{ playback_runs : "run as"
    playback_runs ||--o{ playback_step_results : records
    recording_steps ||--o{ locator_repairs : "repaired by"
    recordings { int id PK  text name UK  text description  text start_url  text created_at  text updated_at }
    recording_steps { int id PK  int recording_id FK  int position  text action_type  text url  text value  int value_is_secret  int requires_approval  text wait_after_json  text metadata_json  text recorded_at }
    step_locators { int id PK  int step_id FK  int rank  text strategy  text value  text tag  text source }
    recording_sessions { text id PK  text state  int tab_id  text start_url  text started_at  text ended_at  int recording_id FK }
    captured_events { int id PK  text session_id FK  int seq  text event_json  text received_at }
    playback_runs { int id PK  int recording_id FK  text actor  text status  text started_at  text ended_at  text message }
    playback_step_results { int id PK  int run_id FK  int step_id  int position  text status  text strategy  text message  text diagnostics_json  text started_at  text ended_at }
    locator_repairs { int id PK  int step_id FK  int run_id  text strategy  text value  int verified  int applied  text created_at }
```

## State machines (State pattern)

```mermaid
stateDiagram-v2
    [*] --> Idle
    Idle --> Recording : start (extension confirmed a tab)
    Recording --> Saving : stop
    Saving --> Idle : save(name) / discard
    state Playback {
        [*] --> Ready
        Ready --> Playing : start
        Playing --> Paused : pause
        Paused --> Playing : resume
        Playing --> Completed : last step ok
        Playing --> Failed : step failed after fallbacks
        Playing --> Blocked : needs approval or a secret nobody gave
        Playing --> Stopped : stop
        Paused --> Stopped : stop
    }
```

An illegal transition (stop while Idle, save while Recording) raises
`IllegalTransition` and changes nothing.

## Architecture

```mermaid
flowchart LR
    subgraph Chrome["Chrome (Windows)"]
        CS["content/*.js<br/>IBrowserEventCapture"] --> SW["sw/*.js<br/>recorder state, backend link,<br/>navigation watcher, indicator"]
        POP["popup<br/>Stop / Save As?"] --> SW
    end
    SW <-->|"WebSocket"| SVC
    subgraph WSL
        SVC["recorder service<br/>IRequestGuard · IExtensionLink"] --> CTRL["IRecordingController<br/>(State: Idle/Recording/Saving)"]
        CTRL --> SESS["IRecordingSessionStore"]
        CTRL --> NORM["IActionNormalizer<br/>(rule pipeline)"]
        CTRL --> REPO["IRecordingRepository"]
        REPO --> DB[("SQLite")]
        SESS --> DB
        MENU["Smart Menu plugin<br/>IPlaybackMenuProvider · IWorkspaceActions"] --> ADW
        AGENT["post office card 'browser-recorder'<br/>AdwSubprocessExecutor"] --> ADW
        ADW["browser_workflow_adw.py<br/>IOperation catalog"] --> CLIENT["IRecorderClient"] --> SVC
        ADW --> PLAY["PlaybackSequence<br/>(Composite of step Commands)"]
        PLAY --> RES["IElementResolver<br/>(Chain over ILocatorStrategy)"]
        PLAY --> CMD["IActionCommandFactory<br/>Commands + Decorators"]
        PLAY --> OBS["IExecutionObserver<br/>run log · console"]
        PLAY --> REC["IPlaybackRecovery<br/>diagnostics → repair proposal"]
        RES --> BA["IBrowserAdapter<br/>SeleniumWorkflowBrowser"]
        CMD --> BA
    end
    BA -->|"CDP :9222"| Chrome
```

## Sequence: recording and saving

```mermaid
sequenceDiagram
    actor U as Person
    participant M as Smart Menu
    participant A as ADW start/stop/save
    participant S as Recorder service
    participant X as Extension (SW + content)
    participant D as SQLite
    U->>M: 2. Start Recording
    M->>A: adw_browser_start_recording.py
    A->>S: POST /recording/start (service spawned if down)
    S->>X: start_recording(session)
    X->>X: active tab, inject capture, badge REC
    X-->>S: recording_started(tab, url)
    S->>D: recording_sessions (Recording)
    S-->>A: started on <url>
    U->>X: click, type, navigate
    X-->>S: event(...) one per action
    S->>D: captured_events
    U->>M: 3. Stop Recording
    M->>A: adw_browser_stop_recording.py
    A->>S: POST /recording/stop
    S->>X: stop_recording
    X-->>S: recording_stopped
    S-->>A: Saving, N actions
    M->>U: Save As?
    U->>M: Open Finance Dashboard
    M->>A: adw_browser_save_recording.py --name
    A->>S: POST /recording/save
    S->>S: normalise, validate
    S->>D: recordings, recording_steps, step_locators
    S-->>A: saved #id
```

## Sequence: playback

`ExecutePlayback` configures `Browser.setDownloadBehavior` with `allow` and
an absolute native download folder before any recorded steps. This covers new
tabs as well as the current tab and avoids operating-system Save As dialogs,
which the extension cannot capture. The default folder is Downloads/BrowserRecorder;
a local `download_directory.txt` or `BROWSER_WORKFLOW_DOWNLOAD_DIR` can override it.
Download configuration errors stop playback before clicks. This currently
configures delivery only; run completion does not verify file completion/content.

```mermaid
sequenceDiagram
    participant C as Menu / ADW / agent
    participant P as PlaybackSequence
    participant D as SQLite
    participant B as Chrome via CDP
    C->>P: execute_playback(name)
    P->>D: load_recording
    P->>P: validate_playback
    P->>B: acquire_session (attach, never launch, never quit)
    loop every step
        P->>B: locate_element (candidates in rank order)
        P->>B: wait_for_element (visible, enabled)
        P->>P: approval gate / secret source if the step needs one
        P->>B: execute_action (Command)
        P->>B: wait_after (url, ready state)
        P->>D: log_result (playback_step_results)
    end
    alt a step fails
        P->>B: diagnose_failure (url, title, screenshot, similar elements)
        P->>D: diagnostics_json; mechanic can read it
    end
    P->>B: release (chromedriver stops; Chrome stays open)
    P-->>C: report_completion
```

## Self-healing, with a person in the loop

1. The step's other recorded locators are tried in rank order (deterministic).
2. If none works, `diagnose_failure` stores url, title, a screenshot, what each
   locator found (nothing, too many, hidden, zero size, disabled), and the page's visible
   interactive elements ranked by similarity to what was recorded.
3. `adw_browser_repair_locator.py --run-id R` proposes the best similar element,
   or takes `--css` from the mechanic or a person. It verifies the proposal on
   the live page by locating only, never clicking. It saves the proposal as
   unapplied.
4. `--apply` (a person's decision) makes it the step's first locator. A
   repair never clicks, and an unverified proposal can't be applied.

## Agents: the `browser-recorder` card in the post office

Toyota, Sydney, Sonnet, Opus and the mechanic reach playback the way they
reach each other: through `lancedb_memory/agent_comms/`. The `browser-recorder` card in
`agents.json` runs on the generic `adw` runtime, `AdwSubprocessExecutor` with
the command `uv run --quiet`, against `adws/adw_browser_agent_request.py`.
The request text goes in as the first argument, and `--verdict` makes the
harness end on a `DONE:` or `REFUSED:` line, which becomes the reply.

    "browser recorder: list playbacks"         -> DONE: saved playbacks: ...
    "have the browser recorder run pay bill"   -> runs it, or REFUSED: why

`AgentRequest` matches the request to one saved playback with
`IPlaybackMatcher`, then runs it with nothing but `{ name, actor, port }`. Its
gates are `DenyApprovalGate` and `NoSecretSource`, so an agent can never
approve a consequential step or supply a password. Those steps block, and the
refusal says which step it was and why, so a person can rerun it from the menu
with `--interactive`. A failed playback is a failed task like any other: the
caller is alerted and `FailureForwarder` sends it to the mechanic. The card is
`idempotent: false`, so it is never retried, because a half-run playback
replayed from the top could submit a form twice.
