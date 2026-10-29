#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Web Search — call a real web-search provider for an already-disambiguated
query, walking a credential ladder across SERP API, Google, and Brave.

The judgement this capability needs — turning what a user actually SAID into
the text a search engine should actually SEE ("Lens" meant "Lance database",
not a camera) — belongs to the agent that is already in conversation with the
user and already has that context. This ADW starts only once that judgement is
done: `--query` is the literal text to search for, already cleaned up, and the
ADW's own job is purely mechanical from there.

Three phases do the mechanical part, the same ladder-then-call shape
`adw_calculate_distance.py`'s `search_address` already proved for SerpAPI on
this box:

* `validate_query` (code) — refuses to spend a network call on an empty or
  whitespace-only query. No agent needed; it is a string check.
* `select_provider` (code) — walks SERP API, then Google Custom Search, then
  Brave, in that order, and takes the first one whose credentials are actually
  set in the environment (`SERP_API_KEY`; `GOOGLE_API_KEY` + `GOOGLE_CSE_ID`;
  `BRAVE_API_KEY`). `--provider` pins one and skips the ladder. Mirrors
  `adw_map_open.py`'s `select_launcher`: probe, report what was probed, take
  the first hit.
* `search` (code) — calls the chosen provider's HTTP API with `urllib`
  (stdlib, no extra dependency) and normalizes its top results to
  {title, link, snippet}. Never raises: a failed or empty search is a reported
  outcome, the same "never raise" rule `calculate_distance.py`'s SerpAPI call
  keeps, because the caller is a conversation, not a shell that wants a
  traceback.

No `kind="agent"` phase: the one part that needed eyes already happened before
this process started, in the agent that built `--query`.

Usage:
    uv run adws/adw_web_search.py "<why this search is being run>" \
        --query "Lance database schema" [--provider serpapi] [--num 5] \
        [--dry-run] [--results-out /tmp/results.json] \
        [--config adws/adw_sssf_config/sssf.config.yaml] [--adw-id a1b2c3d4]

Phases: engineer(request) -> code(validate_query) -> code(select_provider) -> code(search)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from sssf_roles import (ConsoleLifecycleLog, FileOrInlinePromptResolver, IApplication,
                        IArgumentParser, ILifecycleLog, IPhase, IPhaseExecutor,
                        IPhaseHandle, IPromptResolver, LifecycleLogged,
                        ManualContextPhaseExecutor, PrintLifecycleLog,
                        SessionRunFactory, SssfConfigLoader)
from adw_modules.data_types import PhaseParams

REQUIRED_AGENTS: list[str] = []   # entirely code; the query arrives already disambiguated

# Checked in this order -- SERP API first (confirmed working on this box via
# adw_calculate_distance.py), Google Custom Search and Brave as fallbacks that
# cost nothing to probe when unconfigured.
PROVIDER_LADDER: list[tuple[str, tuple[str, ...]]] = [
    ("serpapi",    ("SERP_API_KEY",)),
    ("google_cse", ("GOOGLE_API_KEY", "GOOGLE_CSE_ID")),
    ("brave",      ("BRAVE_API_KEY",)),
]
PROVIDER_NAMES = tuple(name for name, _ in PROVIDER_LADDER)

SEARCH_TIMEOUT = 15      # seconds; matches calculate_distance.py's SerpAPI call
SEARCH_ATTEMPTS = 2      # one retry absorbs a TLS-handshake flake, nothing more


# ─────────────────────────────────────────────────────────────────────────
# Values (not behavior). Plain pydantic models, local to this file -- the
# roster's adw_modules/data_types.py is off-limits to edits, and none of
# these shapes belong anywhere a second ADW would want to import them from.
# ─────────────────────────────────────────────────────────────────────────

class SearchOptions(BaseModel):
    """One ADW invocation. Keeps run() at one parameter."""

    prompt: str                      # why this search is happening; the engineer record
    query: str                       # the literal search text, already disambiguated
    provider: str = ""               # "" = walk the ladder; else pin one of PROVIDER_NAMES
    num_results: int = 5
    dry_run: bool = False
    results_out: str = ""            # write the JSON outcome here, for a non-terminal caller
    config: str = "adws/adw_sssf_config/sssf.config.yaml"
    adw_id: Optional[str] = None


class QueryCheck(BaseModel):
    """validate_query's verdict: is there anything worth spending a call on."""

    raw: str = ""
    query: str = ""
    valid: bool = False
    reason: str = ""


class ProviderChoice(BaseModel):
    """select_provider's verdict: which provider is usable on this box, and what was probed."""

    name: str = ""
    probed: list[str] = Field(default_factory=list)
    found: bool = False
    reason: str = ""


class SearchHit(BaseModel):
    """One normalized result, the same three fields regardless of provider."""

    title: str = ""
    link: str = ""
    snippet: str = ""


class SearchOutcome(BaseModel):
    """search's result: what came back, or why nothing did."""

    provider: str = ""
    hits: list[SearchHit] = Field(default_factory=list)
    succeeded: bool = False
    message: str = ""


# ─────────────────────────────────────────────────────────────────────────
# Provider calls. Each is the only place that knows one provider's URL shape;
# `search_phase` below never does. Plain urllib, stdlib only -- no `requests`
# dependency needed, same choice calculate_distance.py already made.
# ─────────────────────────────────────────────────────────────────────────

def _get_json(url: str, headers: dict[str, str] | None = None) -> dict:
    last_error: Exception | None = None
    for _ in range(SEARCH_ATTEMPTS):
        try:
            request = urllib.request.Request(url, headers=headers or {})
            response = urllib.request.urlopen(request, timeout=SEARCH_TIMEOUT)  # noqa: S310
            try:
                return json.load(response)
            finally:
                response.close()
        except Exception as error:  # noqa: BLE001 - a failed call must become a message, not a crash
            last_error = error
    raise RuntimeError(str(last_error))


def _search_serpapi(query: str, num: int) -> list[SearchHit]:
    params = {"engine": "google", "q": query, "num": num,
             "api_key": os.environ.get("SERP_API_KEY", "").strip()}
    data = _get_json(f"https://serpapi.com/search.json?{urllib.parse.urlencode(params)}")
    return [SearchHit(title=item.get("title", ""), link=item.get("link", ""),
                      snippet=item.get("snippet", ""))
            for item in (data.get("organic_results") or [])[:num]]


def _search_google_cse(query: str, num: int) -> list[SearchHit]:
    params = {"key": os.environ.get("GOOGLE_API_KEY", "").strip(),
             "cx": os.environ.get("GOOGLE_CSE_ID", "").strip(),
             "q": query, "num": min(num, 10)}   # Custom Search caps at 10 per call
    data = _get_json(f"https://www.googleapis.com/customsearch/v1?{urllib.parse.urlencode(params)}")
    return [SearchHit(title=item.get("title", ""), link=item.get("link", ""),
                      snippet=item.get("snippet", ""))
            for item in (data.get("items") or [])[:num]]


def _search_brave(query: str, num: int) -> list[SearchHit]:
    params = {"q": query, "count": num}
    headers = {"Accept": "application/json",
              "X-Subscription-Token": os.environ.get("BRAVE_API_KEY", "").strip()}
    data = _get_json(f"https://api.search.brave.com/res/v1/web/search?{urllib.parse.urlencode(params)}",
                     headers=headers)
    results = (data.get("web") or {}).get("results") or []
    return [SearchHit(title=item.get("title", ""), link=item.get("url", ""),
                      snippet=item.get("description", ""))
            for item in results[:num]]


PROVIDER_SEARCH_FNS = {"serpapi": _search_serpapi, "google_cse": _search_google_cse,
                       "brave": _search_brave}


# ─────────────────────────────────────────────────────────────────────────
# Phases. Each the smallest IPhase that answers its one question, and each
# returns its typed verdict -- ManualContextPhaseExecutor.execute() hands that
# return value straight back to the caller, so the application wires phases
# together with ordinary assignment, never shared mutable state.
# ─────────────────────────────────────────────────────────────────────────

class RequestCapturePhase(LifecycleLogged, IPhase):
    """Records why this search is running and under what options, nothing more."""

    def __init__(self, engineer: str, options: SearchOptions, logger: ILifecycleLog):
        LifecycleLogged.__init__(self, logger)
        self._engineer = engineer
        self._options = options

    @property
    def params(self) -> PhaseParams:
        return PhaseParams(name="request", kind="engineer", owner=self._engineer,
                          description="Record why this web search is being run and under what options")

    def act(self, handle: IPhaseHandle) -> None:
        handle.log(input=self._options.prompt, query=self._options.query,
                  provider=self._options.provider or "(auto)",
                  num_results=self._options.num_results, dry_run=self._options.dry_run)


class ValidateQueryPhase(LifecycleLogged, IPhase):
    """Refuses to spend a network call on nothing."""

    def __init__(self, query: str, logger: ILifecycleLog):
        LifecycleLogged.__init__(self, logger)
        self._query = query

    @property
    def params(self) -> PhaseParams:
        return PhaseParams(name="validate_query", kind="code", owner="websearch",
                          description="Confirm the resolved search text is non-empty before any provider is touched")

    def act(self, handle: IPhaseHandle) -> QueryCheck:
        trimmed = " ".join(self._query.split())
        check = (QueryCheck(raw=self._query, query=trimmed, valid=True, reason="query is usable")
                if trimmed else
                QueryCheck(raw=self._query, query="", valid=False, reason="query is empty after trimming"))
        handle.log(query=check.query or "(empty)", valid=check.valid, reason=check.reason)
        return check


class SelectProviderPhase(LifecycleLogged, IPhase):
    """Walks the SERP API / Google / Brave ladder for the first usable provider."""

    def __init__(self, forced: str, logger: ILifecycleLog):
        LifecycleLogged.__init__(self, logger)
        self._forced = forced

    @property
    def params(self) -> PhaseParams:
        return PhaseParams(name="select_provider", kind="code", owner="websearch",
                          description="Find the first search provider with credentials set on this box")

    def act(self, handle: IPhaseHandle) -> ProviderChoice:
        ladder = PROVIDER_LADDER
        if self._forced:
            ladder = [(name, env_vars) for name, env_vars in PROVIDER_LADDER if name == self._forced]
        probed: list[str] = []
        for name, env_vars in ladder:
            probed.append(name)
            if all(os.environ.get(var, "").strip() for var in env_vars):
                choice = ProviderChoice(name=name, probed=probed, found=True,
                                       reason=f"{', '.join(env_vars)} present in the environment")
                handle.log(**choice.model_dump())
                return choice
        reason = (f"{self._forced!r} is not on the ladder (known: {', '.join(PROVIDER_NAMES)})"
                 if self._forced and not ladder else
                 "no provider on the ladder has its credentials set")
        choice = ProviderChoice(probed=probed, found=False, reason=reason)
        handle.log(**choice.model_dump())
        return choice


class SearchPhase(LifecycleLogged, IPhase):
    """Calls the chosen provider and normalizes its top results. Never raises."""

    def __init__(self, query: str, choice: ProviderChoice, num_results: int,
                dry_run: bool, logger: ILifecycleLog):
        LifecycleLogged.__init__(self, logger)
        self._query = query
        self._choice = choice
        self._num_results = num_results
        self._dry_run = dry_run

    @property
    def params(self) -> PhaseParams:
        return PhaseParams(name="search", kind="code", owner="websearch",
                          description="Call the chosen provider's API and normalize its top results")

    def act(self, handle: IPhaseHandle) -> SearchOutcome:
        if self._dry_run:
            outcome = SearchOutcome(provider=self._choice.name, succeeded=True,
                                   message=f"dry run: would search {self._choice.name!r} for {self._query!r}")
        else:
            try:
                hits = PROVIDER_SEARCH_FNS[self._choice.name](self._query, self._num_results)
            except Exception as error:  # noqa: BLE001 - a failed search is a reported outcome, not a crash
                outcome = SearchOutcome(provider=self._choice.name, succeeded=False,
                                       message=f"{self._choice.name} search failed: {error}")
                handle.log(provider=outcome.provider, hits=0, succeeded=False, message=outcome.message)
                return outcome
            outcome = SearchOutcome(
                provider=self._choice.name, hits=hits, succeeded=bool(hits),
                message=(f"{self._choice.name} returned {len(hits)} result(s) for {self._query!r}" if hits
                        else f"{self._choice.name} returned no results for {self._query!r}"))
        handle.log(provider=outcome.provider, hits=len(outcome.hits),
                  succeeded=outcome.succeeded, message=outcome.message)
        return outcome


# ─────────────────────────────────────────────────────────────────────────
# Adapters. The one new one is argument parsing; config loading, session
# creation, prompt resolution, the executor and both lifecycle sinks are the
# shipped implementations from sssf_roles -- a second copy of any of those
# would be the defect, not the reuse.
# ─────────────────────────────────────────────────────────────────────────

class WebSearchArgumentParser(LifecycleLogged, IArgumentParser):
    """The only class allowed to know this CLI is built on argparse."""

    def __init__(self, description: str, logger: ILifecycleLog):
        LifecycleLogged.__init__(self, logger)
        self._description = description

    def parse(self, argv: list[str]) -> SearchOptions:
        parser = argparse.ArgumentParser(description=self._description)
        parser.add_argument("prompt", help="inline text or a path to a prompt file: why this search is being run")
        parser.add_argument("--query", required=True,
                           help="the literal search text to send to the provider, "
                                "already disambiguated by the calling agent")
        parser.add_argument("--provider", default="", choices=("", *PROVIDER_NAMES),
                           help="pin one provider instead of walking the ladder")
        parser.add_argument("--num", type=int, default=5, help="how many results to return")
        parser.add_argument("--dry-run", action="store_true",
                           help="resolve a provider but make no network call")
        parser.add_argument("--results-out", default="",
                           help="write the JSON outcome here, for a non-terminal caller")
        parser.add_argument("--config", default="adws/adw_sssf_config/sssf.config.yaml")
        parser.add_argument("--adw-id", default=None, help="join or pin an existing session")
        parsed = parser.parse_args(argv)
        return SearchOptions(prompt=parsed.prompt, query=parsed.query, provider=parsed.provider,
                            num_results=parsed.num, dry_run=parsed.dry_run,
                            results_out=parsed.results_out, config=parsed.config,
                            adw_id=parsed.adw_id)


# ─────────────────────────────────────────────────────────────────────────
# Composition root. The only place that is allowed to know every interface
# AND every concrete adapter.
# ─────────────────────────────────────────────────────────────────────────

class WebSearchApplication(LifecycleLogged, IApplication):
    """Orchestrates the three-phase run. Depends only on interfaces (DIP)."""

    def __init__(self,
                argument_parser: IArgumentParser,
                prompt_resolver: IPromptResolver,
                config_loader:   "SssfConfigLoader",
                session_factory: "SessionRunFactory",
                phase_executor:  IPhaseExecutor,
                logger:          ILifecycleLog):
        LifecycleLogged.__init__(self, logger)
        self._argument_parser = argument_parser
        self._prompt_resolver = prompt_resolver
        self._config_loader   = config_loader
        self._session_factory = session_factory
        self._phase_executor  = phase_executor

    def run(self, argv: list[str]) -> int:
        options = self._argument_parser.parse(argv)
        options = options.model_copy(update={"prompt": self._prompt_resolver.resolve(options.prompt)})
        cfg = self._config_loader.load(options.config)
        self._config_loader.validate(cfg, REQUIRED_AGENTS)
        run = self._session_factory.create(cfg, options.adw_id)

        session_log: ILifecycleLog = ConsoleLifecycleLog(run)

        self._phase_executor.execute(run, RequestCapturePhase(run.engineer, options, session_log))
        check = self._phase_executor.execute(run, ValidateQueryPhase(options.query, session_log))

        if check.valid:
            choice = self._phase_executor.execute(run, SelectProviderPhase(options.provider, session_log))
            outcome = (self._phase_executor.execute(
                          run, SearchPhase(check.query, choice, options.num_results, options.dry_run, session_log))
                      if choice.found else
                      SearchOutcome(message=f"no usable search provider: {choice.reason}"))
        else:
            outcome = SearchOutcome(message=f"no search run: {check.reason}")

        if options.results_out:
            Path(options.results_out).write_text(json.dumps(outcome.model_dump(), indent=2) + "\n")

        run.console.note(outcome.message)
        accepted = outcome.succeeded or (options.dry_run and check.valid)
        return run.finish(accepted=accepted, reason=outcome.message)


def build_application() -> IApplication:
    """Wire the concrete adapters into the application. The one spot that may."""
    bootstrap_log: ILifecycleLog = PrintLifecycleLog()
    return WebSearchApplication(
        argument_parser = WebSearchArgumentParser(   __doc__, bootstrap_log ),
        prompt_resolver = FileOrInlinePromptResolver( bootstrap_log         ),
        config_loader   = SssfConfigLoader(           bootstrap_log         ),
        session_factory = SessionRunFactory(          bootstrap_log         ),
        phase_executor  = ManualContextPhaseExecutor( bootstrap_log         ),
        logger          =                             bootstrap_log,        )


if __name__ == "__main__":
    sys.exit(build_application().run(sys.argv[1:]))
