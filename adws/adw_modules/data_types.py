"""Concrete data types for the SSSF ADW system.

RULE (four-param rule): any function that takes more than 4 parameters takes
ONE of these objects instead. AgentCall and PhaseParams are the pattern.

Every agent call declares a concrete output type — an EnvelopeBase subclass —
that its final JSON response is parsed against. No untyped handoffs.
"""

from __future__ import annotations

from typing import Any, Callable, Literal, Optional, Type

from pydantic import BaseModel, Field, ValidationInfo, field_validator

PhaseKind = Literal["engineer", "agent", "code"]
PhaseStatus = Literal["queued", "running", "success", "fail"]


# ── Phases ────────────────────────────────────────────────────────────────────

class PhaseParams(BaseModel):
    """Everything run.phase() needs. Passed as one object, never loose params."""

    name: str                       # short id, unique within the run: "plan", "build"
    kind: PhaseKind                 # which lane the block renders in
    owner: str                      # engineer's name, "git", or an agent name from config
    description: str                # REQUIRED: what this phase does and why — see below
    retries: int = 0                # agent phases: gate-failure retries via continue

    @field_validator("description")
    @classmethod
    def _description_must_be_earned(cls, value: str, info: ValidationInfo) -> str:
        """A phase name identifies; a description explains. Both are required.

        The description is the only sentence the trace, the console, and the
        phase block in the UI ever show about intent — everything else is ids,
        statuses, and timings. `commit_plan: "Commit the plan"` tells a reader
        nothing they could not already see, so an echo is rejected the same way
        a blank one is. This is a construction-time error on purpose: it fires
        before the phase opens, not after a run is already in the trace.
        """
        text = " ".join(value.split())
        name = str(info.data.get("name", "?"))
        if not text:
            raise ValueError(
                f"phase {name!r}: description is required — one sentence on what this "
                f"phase does and why. It is what the trace and the UI show.")
        if text.rstrip(".").casefold() == name.replace("_", " ").casefold():
            raise ValueError(
                f"phase {name!r}: description {text!r} only restates the phase name — "
                f"say what it does and why instead.")
        return text


class Phase(BaseModel):
    """The persisted phase record — PhaseParams plus lifecycle."""

    phase_id: str
    adw_id: str
    seq: int
    params: PhaseParams
    status: PhaseStatus = "fail"    # success must be earned
    attempt: int = 0
    error: Optional[str] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None


# ── Envelopes (agent output types) ───────────────────────────────────────────

class EnvelopeBase(BaseModel):
    """Base of every agent's final JSON response. Output types extend this."""

    status: Literal["success", "fail"]
    summary: str = ""
    artifacts: list[str] = Field(default_factory=list)
    notes_for_next_agent: str = ""


class GenericOutput(EnvelopeBase):
    pass


class PlanOutput(EnvelopeBase):
    # Subject for committing the PLAN — the spec file the planner wrote, not the
    # implementation it describes. Each agent's commit_message covers its own
    # work product, so a chain that commits per step never reuses one agent's
    # words for another agent's diff.
    commit_message: str = ""


class BuildOutput(EnvelopeBase):
    changed_files: list[str] = Field(default_factory=list)
    commit_message: str = ""        # consumed by the git commit phase


class ScoutFinding(BaseModel):
    file: str
    note: str = ""


class ScoutOutput(EnvelopeBase):
    findings: list[ScoutFinding] = Field(default_factory=list)


class ReviewFinding(BaseModel):
    """One thing the request (or plan) asked for, and whether it is there."""

    requirement: str                # the ask, in the requester's words
    met: bool
    evidence: str = ""              # where it lives, or what is missing


class ReviewOutput(EnvelopeBase):
    """Confirmation that what was built is what was asked for — not a test run."""

    approved: bool = False
    findings: list[ReviewFinding] = Field(default_factory=list)
    blocking: list[str] = Field(default_factory=list)   # what must change before approval


class DocumentOutput(EnvelopeBase):
    """Where the write-up of a completed change landed."""

    document_path: str = ""         # the doc in the repo, e.g. app_docs/<adw_id>_<slug>.md
    documented_files: list[str] = Field(default_factory=list)
    commit_message: str = ""


# ── Deterministic quality blocks ─────────────────────────────────────────────

QualityArea = Literal["frontend", "backend"]
QualityOperation = Literal["lint", "typecheck", "build"]


class QualityCheckSpec(BaseModel):
    """One deterministic quality command."""

    name: str
    area: QualityArea
    operation: QualityOperation
    argv: list[str]
    timeout_seconds: int = 120


class QualityCheckResult(BaseModel):
    """Captured evidence from one quality command."""

    name: str
    area: QualityArea
    operation: QualityOperation
    command: str
    returncode: int
    passed: bool
    duration_seconds: float
    output_artifact: str
    # The tail of stdout+stderr, verbatim and unparsed. A failure has to travel
    # back to the builder as an envelope, and the builder cannot open a log file
    # it was never handed — so the evidence rides along. Deliberately raw: every
    # runner formats failures differently and a generic parser would be
    # confidently wrong. The full log is always at output_artifact.
    output_tail: str = ""


class QualityResult(BaseModel):
    """Aggregate result from a quality block: every check it ran, and the verdict."""

    passed: bool
    checks: list[QualityCheckResult] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)


# ── Change capture (git diff, deterministic) ─────────────────────────────────

class ChangeCapture(BaseModel):
    """Everything documentation.capture() needs. One object, never loose params."""

    base: str = "main"              # the ref the work is measured against
    max_diff_lines: int = 2000      # the diff artifact is truncated past this
    include_untracked: bool = True  # a brand-new file is part of the change


class BaseRef(BaseModel):
    """The commit a change is measured from, and why that one.

    `reason` is the line the trace shows. A diff is only as trustworthy as the
    thing it was taken against, so the ADW records that choice instead of
    leaving the reader to infer it.
    """

    ref: str                        # what was asked for: "main", or a pinned sha
    commit: str                     # the commit actually diffed against
    reason: str = ""

    @property
    def label(self) -> str:
        """Display form — a named ref as itself, a pinned raw sha shortened."""
        if len(self.ref) == 40 and all(c in "0123456789abcdef" for c in self.ref):
            return self.ref[:7]
        return self.ref


class ChangeSet(BaseModel):
    """What changed since the base commit — pure git facts, no judgement."""

    base: BaseRef
    files: list[str] = Field(default_factory=list)
    untracked: list[str] = Field(default_factory=list)
    insertions: int = 0
    deletions: int = 0
    stat: str = ""                  # `git diff --stat` output, verbatim
    diff_path: str = ""             # the full diff, written into context_handoff/
    truncated: bool = False

    @property
    def empty(self) -> bool:
        return not (self.files or self.untracked)


class ChangesOutput(EnvelopeBase):
    """A ChangeSet shaped as an envelope so an agent can be handed it directly.

    Same adapter idea as VerifyOutput: code computes the diff, the documenter
    consumes it through the one door every agent handoff uses.
    """

    base: str = ""                  # "<ref> @ <commit> — <reason>"
    changed_files: list[str] = Field(default_factory=list)
    insertions: int = 0
    deletions: int = 0
    stat: str = ""
    diff_path: str = ""             # read this for the full diff


# ── Map opening (deterministic phases over expenses.map_link) ────────────────
# One phase per question, in the order WslMapOpener asks them: is the link
# openable, which launcher is present, did the spawn happen. Each answer is its
# own type so the trace shows WHY a run ended the way it did, and so a future
# agent phase can be inserted between any two of them.

class LauncherSpec(BaseModel):
    """One browser launcher and how to invoke it.

    `argv` is a template list, never a shell string: `{url}` is substituted in
    place, so a stray character in a map link can never become a second
    argument. `name` is what `shutil.which` probes for.
    """

    name: str
    argv: list[str]

    def build(self, url: str) -> list[str]:
        return [arg.replace("{url}", url) for arg in self.argv]


class MapOpenRequest(BaseModel):
    """Everything the map-open phases need. Passed as one object, never loose params."""

    url: str = ""                   # the candidate map_link, exactly as stored
    # What the expense says the destination IS, when the link does not say where
    # it is — a vendor line like "wyoming walmart". A place is not a link, so it
    # never bypasses validate(); it is turned into one by derive_from_place().
    place: str = ""
    dry_run: bool = False           # resolve a launcher, spawn nothing
    # Empty means the module's own ladder. Override to pin or reorder launchers
    # (a test run, a box where explorer.exe must not win).
    launchers: list[LauncherSpec] = Field(default_factory=list)


class MapOpenOptions(BaseModel):
    """One ADW invocation of the map-open chain. Keeps main() at one parameter.

    `resolver` is the extension point: left empty every phase in the chain is
    code, and naming an agent inserts exactly one agent phase — the only step
    that needs eyes. Nothing else in the chain changes shape when it appears.
    """

    prompt: str                     # why this map is being opened; the engineer record
    map_link: str = ""              # expenses.map_link for the expense under review
    place: str = ""                 # the expense's own words for the destination
    receipt: str = ""               # path or text handed to the resolver agent, if any
    resolver: str = ""              # agent name from the config; empty = code-only run
    dry_run: bool = False
    config: str = "adws/adw_sssf_config/sssf.config.yaml"
    adw_id: Optional[str] = None
    # Where to drop the operator's one line, for a caller that is a menu rather
    # than a terminal. The review session reads this file instead of parsing the
    # run's console output, which is formatting and not a contract.
    message_out: str = ""


class PlaceDerivation(BaseModel):
    """What was made of the expense's own words when the stored link failed.

    Kept as its own type rather than folded into LinkCheck so the trace shows
    both halves: the query that was built, and whether it was legible enough to
    be worth a browser at all.
    """

    query: str = ""                 # the search text, normalized
    link: str = ""                  # the maps-search URL built from it; "" when unusable
    usable: bool = False
    reason: str = ""


class LinkCheck(BaseModel):
    """Phase 1's verdict: is this link one we are willing to hand to a browser."""

    raw: str = ""                   # what arrived, untouched
    target: str = ""                # the stripped form we would open; "" when unopenable
    openable: bool = False
    reason: str = ""                # why not, in the words the operator sees
    # Which attempt produced this candidate: the stored column, a search built
    # from the expense's own words, or an agent that read the receipt. The
    # operator's message says so, because a searched location is a guess.
    source: Literal["stored", "place-search", "agent"] = "stored"

    @property
    def shown(self) -> str:
        """The link as it should appear in a message — never an empty gap."""
        return self.raw.strip() or "(empty)"


class LauncherChoice(BaseModel):
    """Phase 2's result: which launcher is present on this box, and what was probed.

    `probed` is kept even on success — the ladder's order is the whole design,
    and a run that fell through to `xdg-open` reads very differently from one
    that took `explorer.exe` first.
    """

    name: str = ""
    argv: list[str] = Field(default_factory=list)
    probed: list[str] = Field(default_factory=list)
    found: bool = False


class LaunchResult(BaseModel):
    """Phase 3's result: did the spawn happen, and the one line the operator gets.

    `message` is the contract the review session consumes — the same strings
    `WslMapOpener.open()` returns, since nothing downstream should have to care
    whether a phase or a method produced them.
    """

    launched: bool = False
    launcher: str = ""
    message: str = ""


class MapLinkOutput(EnvelopeBase):
    """An agent's answer when the stored link is unusable and a human would squint.

    Reading an address off receipt chicken scratch is the one part of this
    workflow a regex loses, so it is the one part that may cost an agent call.
    Everything else here stays code.
    """

    address: str = ""               # what the agent read, in its own words
    map_link: str = ""              # the link it built from that address
    confidence: str = ""            # high | medium | low — the operator's cue to double-check
    # False when the source names no place at all — only codes, only an amount,
    # an unreadable scan. A declared miss, not a failed phase: the agent did the
    # reading it was asked to do. It is the field that lets the gate stop
    # demanding a link the receipt cannot support, which is what an agent under
    # pressure answers by inventing an address.
    locatable: bool = True


class ReceiptLocateRequest(BaseModel):
    """Everything the receipt-locate phases need. Passed as one object, never loose params."""

    receipt_url: str = ""           # the stored (possibly stale) filename or link
    receipts_dir: str = ""          # base dir to search; empty = the module default


class ReceiptCandidate(BaseModel):
    """One file the fuzzy search thinks might be the real receipt."""

    path: str                       # absolute path on disk
    relative: str                   # path relative to the receipts dir, as it is stored
    score: float = 0.0              # token-overlap ratio against the stored filename


class ReceiptSearch(BaseModel):
    """Phase result: what the search found, when the direct lookup failed."""

    resolved_direct: bool = False   # the stored value already pointed at a real file
    direct_path: str = ""
    candidates: list[ReceiptCandidate] = Field(default_factory=list)
    reason: str = ""


class ReceiptSelection(BaseModel):
    """The engineer's answer to "which of these, if any, is the real receipt"."""

    picked: bool = False
    candidate: Optional[ReceiptCandidate] = None


class ReceiptLocateResult(BaseModel):
    """What `main()` hands the caller: the operator's line, and -- only when a
    human confirmed a match -- the corrected `receipt_url` worth persisting."""

    message: str = ""
    receipt_url: str = ""


class VerifyOutput(EnvelopeBase):
    """A deterministic result, shaped as an envelope so an agent can consume it.

    Agents hand each other typed envelopes; code blocks return QualityResult.
    This is the adapter, so a failing lint or test run flows back into the
    builder through exactly the same door a tester agent's report used to —
    the ADW script is the only thing that knows the difference.
    """

    passed: bool = False
    failures: list[str] = Field(default_factory=list)


# ── Calculate distance ──────────────────────────────────────────────────────

class DistanceOptions(BaseModel):
    """One ADW invocation of the calculate-distance chain. Mirrors MapOpenOptions."""

    prompt: str                     # why this distance is being calculated
    map_link: str = ""              # expenses.map_link, read for a destination hint
    place: str = ""                 # the expense's own words for the destination
    receipt: str = ""               # path or text handed to the resolver agent, if any
    resolver: str = ""              # agent name from the config; empty = code-only run
    origin: str = ""                # defaults to the Gratiot home address when blank
    dry_run: bool = False
    config: str = "adws/adw_sssf_config/sssf.config.yaml"
    adw_id: Optional[str] = None
    message_out: str = ""
    map_link_out: str = ""           # where to write a newly-derived map_link, if any
    # Addresses already on file for this vendor that the caller wants a
    # DIFFERENT branch than -- the review menu's "Calculate the next nearest
    # address" option, searching past every known location instead of
    # reporting the same one back. Forces `locate_nearest` to run even when
    # `calculate()` already found a plausible route, and filters its
    # candidates against this list.
    exclude_addresses: list[str] = Field(default_factory=list)


class DistanceRequest(BaseModel):
    """Everything the calculate-distance phases need. Passed as one object."""

    origin: str = ""
    destination: str = ""           # the candidate address/place text, as typed into Maps
    place: str = ""                 # the expense's own words, before any derivation
    dry_run: bool = False
    exclude_addresses: list[str] = Field(default_factory=list)


class DestinationCheck(BaseModel):
    """Phase 1's verdict: is there already a destination worth driving Maps with."""

    candidate: str = ""
    usable: bool = False
    reason: str = ""


class DestinationOutput(EnvelopeBase):
    """An agent's answer when neither the stored link nor a search found a
    confident destination and a human would squint at the receipt instead."""

    address: str = ""
    confidence: str = ""            # high | medium | low
    locatable: bool = True          # False: the source names no place at all


class DistanceResult(BaseModel):
    """What was read off the Maps directions page, and the operator's line."""

    distance_text: str = ""
    duration_text: str = ""
    found: bool = False
    message: str = ""


class NearestLocationResult(BaseModel):
    """What was read off a Maps *search* result, when a chain's name alone
    ("MISTER CAR WASH #0465 GRAND RAPIDS MI") would not route as typed and
    the nearest actual branch has to be looked up instead."""

    found: bool = False
    name: str = ""                  # the result card's own name
    address: str = ""               # partial, no city/state -- display only
    destination: str = ""           # "lat,lng" -- fed straight into the directions box
    message: str = ""


class SearchAddressResult(BaseModel):
    """What SerpAPI's Google Search found for a destination neither the
    directions box nor a Maps search near the origin could resolve --
    typically a knowledge-graph or local-results address for the business's
    own listing."""

    found: bool = False
    name: str = ""                  # the listing's own title, if present
    address: str = ""               # a full street address, usable as a destination
    message: str = ""


# ── Receipt address (OCR, with an agent fallback) ───────────────────────────

class ReceiptAddressOptions(BaseModel):
    """One ADW invocation of the receipt-address chain. Mirrors DistanceOptions."""

    prompt: str                     # why this address is being read
    receipt_path: str = ""          # path to the receipt image to read
    resolver: str = ""              # agent name from the config; empty = OCR only
    config: str = "adws/adw_sssf_config/sssf.config.yaml"
    adw_id: Optional[str] = None
    message_out: str = ""
    address_out: str = ""           # where to write the resolved address, if any


class ReceiptAddressRequest(BaseModel):
    """Everything the receipt-address phases need. Passed as one object."""

    receipt_path: str = ""


class AddressExtraction(BaseModel):
    """The mechanical OCR pass's verdict: is there already one confident
    street-address line in the text, or is the read too fuzzy to trust."""

    candidate: str = ""
    confident: bool = False
    reason: str = ""


class ReceiptAddressResult(BaseModel):
    """What `main()` hands the caller: the resolved address (if any), where
    it came from, and the operator's one line."""

    address: str = ""
    source: str = ""                # "ocr" | "agent" | "none"
    message: str = ""


# ── Verify expense (vendor key / category agreement) ────────────────────────

class VerifyExpenseOptions(BaseModel):
    """One ADW invocation of the per-expense verification chain."""

    prompt: str                     # why this expense is being verified
    expense_id: int = 0
    judge: str = ""                 # agent that weighs a multi-category row; "" = code only
    config: str = "adws/adw_sssf_config/sssf.config.yaml"
    adw_id: Optional[str] = None
    message_out: str = ""           # the operator's one line
    verdict_out: str = ""           # the full verdict as JSON, for a menu caller


class SurveyExpensesOptions(BaseModel):
    """One ADW invocation of the whole-season survey.

    No `judge` field, deliberately. The survey is code-only: a judgement is
    worth paying for when a person is about to act on it, and nobody acts on a
    survey. The interactive pass is where the judge earns its tokens.
    """

    prompt: str                     # why the season is being surveyed
    year: int = 2025
    kind: str = "all"               # all | unverified | backlog
    limit: int = 0                  # stop after N expenses; 0 = the whole season
    resume: bool = False            # continue after the journal's last settled position
    run_id: str = ""                # journal file name; "" = the adw id
    config: str = "adws/adw_sssf_config/sssf.config.yaml"
    adw_id: Optional[str] = None
    message_out: str = ""


class CategoryOpinion(EnvelopeBase):
    """A reader's answer when a vendor permits several categories and the row's
    description has to be weighed to tell which one happened.

    `abstained` is the expected answer, not the failure case. A card feed line
    that says "MEIJER STORE 020" supports nothing, and an opinion invented on
    top of it is worse than no opinion -- the gate lets an abstention through
    precisely so there is no incentive to guess.
    """

    category_id: Optional[int] = None
    abstained: bool = True
    reading: str = ""               # the words in the evidence that decided it


# ── Agent calls ──────────────────────────────────────────────────────────────

class GateCheck(BaseModel):
    """One thing a gate looked at, and what it found.

    `note` is the evidence — "exists, 2.1KB", "exit 0", "not in the diff". On a
    failed check it doubles as the reason, so it is what the agent is told.
    """

    item: str                       # what was checked: a path, a command, a test
    ok: bool
    note: str = ""


class GateReport(BaseModel):
    """What every gate returns: the checks it ran. Violations are derived.

    Authoring stays a one-liner per item — `report.check(...)` appends and
    returns self, so a gate is a loop and a return.
    """

    checks: list[GateCheck] = Field(default_factory=list)

    def check(self, item: str, ok: bool, note: str = "") -> "GateReport":
        self.checks.append(GateCheck(item=item, ok=ok, note=note))
        return self

    @property
    def violations(self) -> list[str]:
        return [f"{c.item}: {c.note or 'failed'}" for c in self.checks if not c.ok]

    @property
    def passed(self) -> bool:
        return not self.violations


class AgentCall(BaseModel):
    """One agent invocation: prompt in, typed envelope out, gates verified."""

    model_config = {"arbitrary_types_allowed": True}

    output_type: Type[EnvelopeBase]
    prompt: str
    previous: Optional[EnvelopeBase] = None
    gates: list[Callable] = Field(default_factory=list)   # gate(envelope, run) -> list[str]


# ── Config ───────────────────────────────────────────────────────────────────

class PromptEngineering(BaseModel):
    system: str                     # path to system.md
    user: str                       # path to user.md


class AgentConfig(BaseModel):
    name: str
    coding_agent: Literal["pi", "claude_code"] = "pi"
    model: str = "google/gemini-3.6-flash"
    thinking: str = "medium"        # off | minimal | low | medium | high | xhigh | max
    color: str = ""                 # hex swatch for this agent's lane in the UI
    purpose: str = ""
    prompt_engineering: PromptEngineering
    harness_engineering: list[str] = Field(default_factory=list)
    tools: Optional[list[str]] = None    # allowlist; None = all tools usable
    # What this agent may MODIFY in the repo, enforced in code after every call
    # (see adw_modules/permissions.py). `tools` cannot express this: `bash` runs
    # anything and `write` reaches any path, so an agent's capability list is a
    # statement of intent that nothing checks.
    #   None  -> unrestricted, except the roster-wide `protected_files` paths
    #   []    -> read-only: may modify nothing tracked
    #   [...] -> only these. A trailing "/" means a directory prefix; a "*"
    #            makes it a glob; anything else is an exact path.
    writes: Optional[list[str]] = None


class ConfigDefaults(BaseModel):
    coding_agent: Literal["pi", "claude_code"] = "pi"
    model: str = "google/gemini-3.6-flash"
    thinking: str = "medium"
    color: str = ""
    harness_engineering: list[str] = Field(default_factory=list)
    tools: Optional[list[str]] = None    # roster-wide allowlist; None = all tools usable
    # Off-limits to every agent that has not named them in its own `writes`.
    # The factory's own code is the default: an agent must not be able to edit
    # the machinery that decides whether its work passed.
    protected_files: list[str] = Field(default_factory=lambda: [
        "adws/adw_modules/", "adws/adw_sssf_config/", "adws/adw_*.py",
    ])
    data_dir: str = "adws/adw_data"


class ObservabilityConfig(BaseModel):
    db: str = "adws/adw_data/sssf.db"
    poll_ms: int = 500


class SSSFConfig(BaseModel):
    defaults: ConfigDefaults = Field(default_factory=ConfigDefaults)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    agents: list[AgentConfig] = Field(default_factory=list)


# ── Tracing ──────────────────────────────────────────────────────────────────

class EventRecord(BaseModel):
    """One traced event, always logged against adw_id + phase."""

    adw_id: str
    phase_id: str = ""
    type: str                       # phase_start | agent_start | tool_call | handoff | gate_pass | gate_fail | log | agent_end | phase_end | error
    name: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    parent_id: str = ""
    tokens: Optional[int] = None
    # Spans: set both when an event covers real elapsed time (a tool call), so
    # the UI lays it out on a time axis without parsing payload JSON. Left unset,
    # the tracer stamps started_at with the moment the event was recorded.
    started_at: Optional[str] = None
    ended_at: Optional[str] = None


# ── Pi coding agent interface ────────────────────────────────────────────────

class PiRequest(BaseModel):
    """Everything one non-interactive pi run needs."""

    prompt: str
    system_prompt: str
    model: str                      # registry pattern, resolved to provider + id
    thinking: str = "medium"
    session_id: str                 # pi --session-id: creates or continues
    session_dir: str
    raw_output_path: str            # JSONL stream lands here
    tools: Optional[list[str]] = None
    extensions: list[str] = Field(default_factory=list)
    cwd: str = "."                  # set from run.repo_root — the codebase root agents work in


class UsageBreakdown(BaseModel):
    """Tokens and the dollars they cost, per component, summed over a call.

    Mirrors pi's `usage` shape one-for-one so the numbers reconcile with what
    pi itself reports: `input` EXCLUDES cache reads, which bill at their own
    (cheaper) rate — add them to learn the size of the prompt that was sent.
    """
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    # Thinking tokens. NOT a fifth component: measured across every session on
    # disk, reasoning is always <= output and the four components above always
    # sum to totalTokens, so reasoning is the thinking SHARE of output, billed
    # at the output rate. Report it nested under output, never added to it.
    reasoning_tokens: int = 0
    total_tokens: int = 0
    input_cost: float = 0.0
    output_cost: float = 0.0
    cache_read_cost: float = 0.0
    cache_write_cost: float = 0.0
    total_cost: float = 0.0

    def add_turn(self, usage: dict, total_tokens: int) -> None:
        """Fold in one pi `message_end` usage object.

        `total_tokens` is passed in rather than re-derived: the caller already
        computes it pi's way (totalTokens, else the sum of the parts).
        """
        cost = usage.get("cost") or {}
        self.input_tokens += usage.get("input") or 0
        self.output_tokens += usage.get("output") or 0
        self.cache_read_tokens += usage.get("cacheRead") or 0
        self.cache_write_tokens += usage.get("cacheWrite") or 0
        self.reasoning_tokens += usage.get("reasoning") or 0
        self.total_tokens += total_tokens
        self.input_cost += cost.get("input") or 0.0
        self.output_cost += cost.get("output") or 0.0
        self.cache_read_cost += cost.get("cacheRead") or 0.0
        self.cache_write_cost += cost.get("cacheWrite") or 0.0
        self.total_cost += cost.get("total") or 0.0

    def merge(self, other: "UsageBreakdown") -> None:
        """Add another call's usage — a phase that retries spends more than once."""
        for field in self.model_fields:
            setattr(self, field, getattr(self, field) + getattr(other, field))


class PiResult(BaseModel):
    text: str = ""
    returncode: int = 0
    session_id: str = ""
    tokens: int = 0
    cost: float = 0.0
    usage: UsageBreakdown = Field(default_factory=UsageBreakdown)
    # Context occupancy after the LAST turn — not a sum. `tokens` bills every
    # turn; this is how full the window is right now, which is what the
    # visualizer's context bar measures against `context_window`.
    context_tokens: int = 0
    context_window: int = 0         # 0 when the registry declares no ceiling


# ── Debug console (adw_debug_console.py) ─────────────────────────────────────

ActionScope = Literal["adw", "phase", "inspect", "session"]
ActionOutcome = Literal["success", "fail", "not_run"]


class ActionSpec(BaseModel):
    """One menu-selectable operation, described before it is run.

    The console renders these; a web dashboard would serialize them. Neither
    needs to know what the action DOES, which is why scope/phase_name/owner
    are data here rather than strings baked into a menu label.
    """

    action_id: str
    title: str
    scope: ActionScope              # whole ADW | one phase | inspection | session lifecycle
    description: str                # doubles as the PhaseParams description
    phase_name: str = ""            # the SSSF phase this action opens, if any
    phase_kind: Optional[PhaseKind] = None
    owner: str = ""
    needs_path: bool = False        # the console must collect an input first
    requires: list[str] = Field(default_factory=list)   # prerequisite ids


class ActionPreview(BaseModel):
    """What the console shows BEFORE executing, so a selection is never blind."""

    spec: ActionSpec
    inputs: dict[str, Any] = Field(default_factory=dict)
    ready: bool = True
    missing: list[str] = Field(default_factory=list)    # unmet prerequisites


class ActionRequest(BaseModel):
    """Four-param rule: invoke() takes one object, not loose args."""

    action_id: str
    path: str = ""                  # only read when spec.needs_path


class ActionResult(BaseModel):
    """What happened. `not_run` is a third outcome, distinct from `fail`.

    A missing prerequisite must not look like a failed phase: nothing was
    attempted, so no phase is opened and none is recorded. Keeping it separate
    is what stops the trace from filling with phantom failures while the
    engineer learns the order of the steps.
    """

    action_id: str
    outcome: ActionOutcome
    summary: str = ""
    detail: list[str] = Field(default_factory=list)
    state_changed: list[str] = Field(default_factory=list)
    elapsed_ms: int = 0
    adw_id: str = ""
    phase_id: str = ""              # empty for inspect actions and not_run
    log_path: str = ""              # where to read the trace for this action
    error: str = ""


class DemoStateView(BaseModel):
    """Serializable snapshot of DemoSession — the part that crosses a boundary.

    DemoSession itself stays a plain object because it holds a live file
    handle; this is what the console prints and a web panel would render.
    """

    adw_id: str = ""
    selected_path: str = ""
    handle_open: bool = False
    opened_at: str = ""
    read_at: str = ""
    byte_count: Optional[int] = None
    line_count: Optional[int] = None
    has_text: bool = False
    phases_run: list[str] = Field(default_factory=list)
