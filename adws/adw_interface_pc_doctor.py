#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Interface PC Doctor -- the dashboard's PC Monitor tab is blinking for
this box; check it, clean it, check again, dig deeper, and if it is still
blinking, say so loudly rather than pretend.

The trigger is the main dashboard's "System Status" -> "PC Monitor" ->
"Windows 10" / "Windows 11" tab going yellow ('warn') or red ('crit'). That
colour comes from one place, `GET /api/pc-metrics?pc=win10|win11` on the
dashboard (letta-code/dashboard/monitoring/pc_metrics.py), and this ADW reads
the same endpoint before and after every step. "Did the criticalness go away"
is therefore answered by the exact number that made the tab blink, not by a
second opinion that could disagree with it.

Escalation, one rung at a time:

* `status_before` (code) -- read the dashboard. Already 'ok' means there is
  nothing to treat, and the run says so and stops (unless `--even-if-ok`).
* `survey` (code) -- look before anyone scans. `robocopy /L` (list mode:
  it reads directory entries and copies nothing) sizes every top-level
  folder on C:, the home folder's children and AppData\\Local's children,
  several at a time and each capped at 45s; a folder that cannot be listed
  in that time is marked "too big to list" instead of waited on. Measured on
  this box: AppData\\Local, 219k files / 178 GB, in 12.5s, where Git Bash's
  `du` over the same tree ran for many minutes. The map goes to both Sonnet
  runs with rules (no recursive scan of anything over 50,000 files, one level
  at a time, `timeout 60` on every command), so they start from what is
  already known instead of re-walking the disk blind.
* `estimate` (code) -- how long the whole ladder is likely to take, from the
  phase timings of this ADW's earlier runs (or defaults, with none). Over
  `--budget-minutes` (30) and the user gets the same Windows toast the towel
  alert uses, up front, with the estimate in it -- not a surprise half an
  hour later.
* `pc_check` (agent) -- Sonnet, through the *Windows* Claude Code CLI, runs
  the `/pc-check` command. The command's text is read from
  `C:\\Users\\NewUser\\Desktop\\.claude\\commands\\pc-check.md` when it is there,
  else from the user-level `C:\\Users\\NewUser\\.claude\\commands\\pc-check.md`
  (where it lives today on both boxes, in two different versions), and the
  phase logs which one ran. It is Windows Claude, not WSL's, because the
  command is written for Git Bash paths (`/c/Users/...`) and PowerShell.
  Read-only: it reports; it is told not to delete.
* `plan_cleanup` / `cleanup` (code) -- previews first (sizes every approved
  item and logs the total before anything is touched), then deletes: temp folders, crash dumps, browser and
  package caches, and the `node_modules` / `.venv` of projects nobody has
  touched in `--idle-days`. Code does the deleting, never a model, and every
  path goes through `RegenerableOnlyPolicy` first.
* `status_after_cleanup` (code) -- the dashboard again. 'ok' ends the run.
* `dig_deeper` (agent) -- Sonnet again, handed the first report, what the
  cleanup freed, and the reading that is still red. It may *propose*
  deletions; `apply_proposals` (code) puts each one through the same policy,
  so a proposal to delete something irreplaceable becomes a recommendation
  instead of a deletion. Agent proposes, code disposes.
* `status_after_dig` (code) -- the dashboard a third time.
* `alert_user` (code) -- still not 'ok': throw in the towel. A Windows toast,
  an ALERT.md in the session folder, and a non-zero exit, carrying the
  agent's recommendations for the drastic measures this ADW will not take on
  its own (emptying the Recycle Bin, moving personal files, compacting WSL's
  ext4.vhdx -- which needs `wsl --shutdown` and so takes down everything
  running in WSL).
* `ask_compact` (code) -- the last of those is offered, not taken: the
  dashboard raises a Yes/No "Run WSL Compact?" modal (its /api/adw-prompt,
  which only records the click) and this ADW waits `--answer-minutes` (15)
  for it. No, or no answer: abort and exit, nothing else to do.
* `run_compact` (code) -- Yes: run adw_interface_wsl_compact.py, which stages
  a Windows-side worker and returns; this run then finishes at once, because
  that worker shuts WSL -- and this process -- down a grace period later.

The budget is a deadline, not a hope. Each Sonnet run gets a slice of what
is left of it, and on expiry its whole Windows process tree -- cmd.exe,
claude.exe and every du.exe it spawned -- is found by the `--session-id`
tagged onto its command line and killed with `taskkill /T /F`. Killing only
the WSL-side child is not enough: the Windows grandchildren keep the output
pipe open and the wait for it never returns, which is how a run sat past 30
minutes until it was killed by hand. A timed-out check is not fatal (the
cleanup needs no report); a budget spent before `dig_deeper` goes straight to
the alert, which says the time ran out.

One fact shapes what "clean" can achieve, and the cleanup report states it
rather than letting a reader discover it: the dashboard measures C:, and
WSL's disk is one ext4.vhdx file on C: that is NOT sparse. Deleting a venv
inside WSL frees space inside that file and none on C:, so only the
Windows-side half of the cleanup can move the bar. The WSL half still runs,
because it was asked for and it keeps WSL itself from filling up.

This ADW runs ON the box it treats -- it deletes local files -- so `--pc`
defaults to whichever monitor this hostname is, and naming the other box is
refused instead of reaching across Tailscale to delete there.

Usage (from /home/adamsl/agent_blocks):
    uv run adws/adw_interface_pc_doctor.py "<why>" [--pc win10|win11] \\
        [--dry-run] [--no-agent] [--even-if-ok] [--idle-days 45] [--budget-minutes 30] \\
        [--no-compact-offer] [--answer-minutes 15] \\
        [--dashboard-url http://100.102.209.100:8765] [--message-out /tmp/line.txt] \\
        [--config adws/adw_sssf_config/sssf.config.yaml] [--adw-id a1b2c3d4]

Phases: engineer(request) -> code(status_before) -> code(survey) -> code(estimate) -> agent(pc_check)
        -> code(plan_cleanup) -> code(cleanup) -> code(status_after_cleanup)
        [-> agent(dig_deeper) -> code(apply_proposals) -> code(status_after_dig)
        [-> code(alert_user) [-> code(ask_compact) [-> code(run_compact)]]]]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import statistics
import time
import urllib.request
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

from sssf_roles import ( ConsoleLifecycleLog, IApplication, IArgumentParser, IConfigLoader,
                         ILifecycleLog, IPhase, IPhaseExecutor, IPhaseHandle, ISessionFactory,
                         LifecycleLogged, ManualContextPhaseExecutor, PrintLifecycleLog,
                         SessionRunFactory, SssfConfigLoader )

from adw_modules import utils
from adw_modules.data_types import PhaseParams
from adw_modules.runner import Run

REQUIRED_AGENTS: list[ str ] = []   # the agents here are Claude Code CLI runs, not pi roster agents

DEFAULT_DASHBOARD_URL = os.environ.get( "PC_DOCTOR_DASHBOARD_URL", "http://100.102.209.100:8765" )

#: Which dashboard monitor each box is. The keys are the dashboard's own
#: PC_MONITORS keys; a box not listed here has nothing on the tab to treat.
MONITOR_FOR_HOST: dict[ str, str ] = {
    "DESKTOP-SHDBATI": "win10",
    "DESKTOP-2OBSQMC": "win11",
}

WIN_HOME = Path( os.environ.get( "PC_DOCTOR_WINDOWS_HOME", "/mnt/c/Users/NewUser" ))
WSL_HOME = Path.home()

#: Where /pc-check's text is looked for, first hit wins.
PC_CHECK_CANDIDATES: tuple[ Path, ... ] = (
    WIN_HOME / "Desktop/.claude/commands/pc-check.md",
    WIN_HOME / ".claude/commands/pc-check.md",
)
#: Claude Code's working directory, so the Desktop's .claude/settings.local.json
#: (which already allows wmic/du) is the project config it picks up.
CLAUDE_CWD = WIN_HOME / "Desktop"
CLAUDE_MODEL = "sonnet"
CLAUDE_TOOLS = "Bash,Read,Glob,Grep,PowerShell"

#: Survey: each folder's listing is cut off after this long, this many at once.
SURVEY_FOLDER_SECONDS = 45
SURVEY_PARALLEL = 6
#: A folder with more files than this is never to be scanned recursively.
SCAN_FILE_LIMIT = 50_000

DEFAULT_BUDGET_MINUTES = 30
#: pc_check may spend at most this share of the budget, so dig_deeper is not
#: starved by a first check that wandered.
PC_CHECK_BUDGET_SHARE = 0.4
#: Kept back from every agent slice for the code phases after it (cleanup,
#: two dashboard reads with their cache waits, the alert).
CODE_RESERVE_SECONDS = 3 * 60
#: Phase durations (seconds) assumed when no earlier run has timed them.
DEFAULT_PHASE_SECONDS: dict[ str, float ] = {
    "status_before": 3, "survey": 120, "pc_check": 10 * 60, "plan_cleanup": 30, "cleanup": 60,
    "status_after_cleanup": 15, "dig_deeper": 12 * 60, "vet_proposals": 5,
    "apply_proposals": 60, "status_after_dig": 15, "alert_user": 5,
}

#: The dashboard caches a reading for 10s (PC_METRICS_CACHE_TTL); waiting a
#: little longer than that is what makes "after" a new sample, not the old one.
DASHBOARD_CACHE_WAIT_SECONDS = 12
#: Still blinking at the end: the dashboard asks this, and Yes runs the compaction ADW.
COMPACT_QUESTION = "Run WSL Compact?"
COMPACT_ADW = Path( __file__ ).resolve().parent / "adw_interface_wsl_compact.py"
DEFAULT_ANSWER_MINUTES = 15


# -- Values (not behavior) ---------------------------------------------------

Level = Literal[ "ok", "warn", "crit" ]


@dataclass( frozen=True )
class PcDoctorRequest:
    """Parsed command-line intent. Mirrors sssf_roles.PromptRequest's shape."""
    prompt:        str
    pc:            str
    dashboard_url: str
    dry_run:       bool
    no_agent:      bool
    even_if_ok:    bool
    idle_days:     int
    budget_minutes: int
    offer_compact: bool
    answer_minutes: int
    message_out:   str
    config:        str
    adw_id:        str | None


class MetricReading( BaseModel ):
    """One bar on the dashboard card, as the dashboard reported it."""
    key:   str
    label: str
    level: Level
    text:  str


class PcStatus( BaseModel ):
    """The dashboard's verdict on one box. `level` is what colours the tab."""
    key:     str
    label:   str
    level:   Level
    metrics: list[ MetricReading ]

    @property
    def healthy( self ) -> bool:
        return self.level == "ok"

    def describe( self ) -> str:
        alarms = [ f"{ m.label } { m.text } ({ m.level })" for m in self.metrics if m.level != "ok" ]
        return f"{ self.label } is { self.level }" + ( f": { '; '.join( alarms )}" if alarms else "" )


class AgentReply( BaseModel ):
    """One headless Claude Code run, reduced to what the phases need."""
    ok:       bool
    text:     str
    cost_usd: float = 0.0
    error:    str = ""
    timed_out: bool = False


class RunEstimate( BaseModel ):
    """How long the full escalation ladder is expected to take."""
    seconds: float
    source:  str
    per_phase: dict[ str, float ]

    @property
    def minutes( self ) -> int:
        return max( 1, round( self.seconds / 60 ))


class RunBudget:
    """The run's deadline. A clock, so a tiny class rather than a role."""

    def __init__( self, minutes: int ):
        self.minutes  = minutes
        self.deadline = time.monotonic() + minutes * 60

    def remaining( self ) -> float:
        return max( 0.0, self.deadline - time.monotonic())


class SurveyEntry( BaseModel ):
    """One folder (or one big system file) as robocopy list mode counted it."""
    path:     str
    dirs:     int = 0
    files:    int = 0
    bytes:    int = 0
    complete: bool = True    # False: cut off at the time limit, counts unknown
    seconds:  float = 0.0


class DiskSurvey( BaseModel ):
    """The map handed to the agents: what is big, and what is too big to walk."""
    entries:    list[ SurveyEntry ] = Field( default_factory=list )
    c_total:    int = 0
    c_free:     int = 0
    seconds:    float = 0.0
    file_limit: int = SCAN_FILE_LIMIT

    @property
    def too_big( self ) -> list[ SurveyEntry ]:
        return [ e for e in self.entries if not e.complete or e.files > self.file_limit ]

    def to_prompt( self, top: int = 45 ) -> str:
        counted = sum( e.bytes for e in self.entries if e.complete )
        used = self.c_total - self.c_free
        rows = sorted( self.entries, key=lambda e: ( e.complete, -e.bytes ))[ :top ]
        lines = [ f"C: { _gb( used )} used of { _gb( self.c_total )}, { _gb( self.c_free )} free. "
                  f"Folders below account for { _gb( counted )} (C:\\Windows over-counts: its hard links "
                  f"are counted once per name). Measured in { self.seconds:.0f}s.", "",
                  "      size       files  folder" ]
        for e in rows:
            if not e.complete:
                lines.append( f"   UNKNOWN           ?  { e.path }   <- TOO BIG TO LIST in "
                              f"{ SURVEY_FOLDER_SECONDS }s: never scan it recursively" )
            else:
                flag = "   <- over the scan limit" if e.files > self.file_limit else ""
                lines.append( f"{ _gb( e.bytes ):>10} { e.files:>11,}  { e.path }{ flag }" )
        return "\n".join( lines )


SCAN_RULES = """A disk map was already measured for you (below). Use it instead of
re-measuring. These rules OVERRIDE any scan step above:
- Never run du, Get-ChildItem -Recurse, or any other recursive scan on a folder
  the map lists with more than {limit:,} files or marks TOO BIG TO LIST. Go one
  level down at a time instead, and size each child on its own.
- Size folders with robocopy in list mode, from PowerShell (it copies nothing
  and is many times faster than du here):
    robocopy 'C:\\some\\folder' C:\\nul /L /S /NJH /NFL /NDL /BYTES /XJ /R:0 /W:0
  From Git Bash, prefix it with MSYS_NO_PATHCONV=1 or the /L-style flags get
  rewritten into paths.
- Put `timeout 60` in front of every Bash command. If something hits a
  limit, say so in your report and move on; do not retry it bigger.
- Do not re-measure anything the map already answers.

{map}
"""


class PcCheckCommand( BaseModel ):
    """The /pc-check command file that was found, and its body."""
    path: str
    text: str


Side = Literal[ "windows", "wsl" ]
Mode = Literal[ "contents", "whole" ]


class CleanupItem( BaseModel ):
    """One thing to delete. `contents` empties a folder but keeps it (Temp);
    `whole` removes the path itself (a stale node_modules)."""
    path:          str
    mode:          Mode
    min_age_hours: float = 0.0
    reason:        str

    @property
    def side( self ) -> Side:
        return "windows" if self.path.startswith( "/mnt/" ) else "wsl"


class CleanupResult( BaseModel ):
    item:        CleanupItem
    freed_bytes: int = 0
    error:       str = ""


class CleanupReport( BaseModel ):
    results:          list[ CleanupResult ] = Field( default_factory=list )
    refused:          list[ str ] = Field( default_factory=list )
    dry_run:          bool = False
    c_free_before:    int = 0
    c_free_after:     int = 0
    wsl_free_before:  int = 0
    wsl_free_after:   int = 0

    def freed( self, side: Side ) -> int:
        return sum( r.freed_bytes for r in self.results if r.item.side == side )

    def summary( self ) -> str:
        verb = "would free" if self.dry_run else "freed"
        lines = [
            f"Cleanup { verb } { _gb( self.freed( 'windows' ))} on the Windows side and "
            f"{ _gb( self.freed( 'wsl' ))} inside WSL.",
            f"C: free went { _gb( self.c_free_before )} -> { _gb( self.c_free_after )}.",
            "WSL space is freed inside ext4.vhdx only; that file is not sparse, so it "
            "does not come back to C: until the disk is compacted.",
        ]
        if self.refused:
            lines.append( f"{ len( self.refused )} path(s) were refused by the deletion policy." )
        biggest = sorted( self.results, key=lambda r: r.freed_bytes, reverse=True )[ :10 ]
        lines += [ f"  { _gb( r.freed_bytes ):>9}  { r.item.path }" for r in biggest if r.freed_bytes ]
        return "\n".join( lines )


class ProposedDeletion( BaseModel ):
    path:   str
    reason: str = ""


class DeeperFindings( BaseModel ):
    """What dig_deeper hands back. Parsed from the reply's last ```json block."""
    summary:         str = ""
    deletions:       list[ ProposedDeletion ] = Field( default_factory=list )
    recommendations: list[ str ] = Field( default_factory=list )


def _gb( n: int ) -> str:
    return f"{ n / 1024 ** 3:.2f} GB"


# -- Roles -------------------------------------------------------------------

class IPcStatusSource( ABC ):
    """Where a box's ok/warn/crit comes from."""

    @abstractmethod
    def read( self, pc: str ) -> PcStatus: ...


class IClaudeAgent( ABC ):
    """One prompt in, one finished agent reply out."""

    @abstractmethod
    def ask( self, prompt: str, timeout_seconds: float, tag: str ) -> AgentReply:
        """Must return by the timeout, having stopped everything it started."""


class ICommandSource( ABC ):
    """Finds the /pc-check command's text."""

    @abstractmethod
    def load( self ) -> PcCheckCommand: ...


class IDeletionPolicy( ABC ):
    """The one gate every deletion passes. None means allowed."""

    @abstractmethod
    def refusal( self, item: CleanupItem ) -> str | None: ...


class ICleanupPlanner( ABC ):
    """Proposes cleanup items of one kind. Proposes only; the policy decides."""

    @abstractmethod
    def plan( self ) -> list[ CleanupItem ]: ...


class IRemover( ABC ):
    """Deletes (or, dry, measures) approved items."""

    @abstractmethod
    def remove( self, items: list[ CleanupItem ], dry_run: bool ) -> list[ CleanupResult ]: ...


class IRunEstimator( ABC ):
    """Predicts the run's length before any slow step starts."""

    @abstractmethod
    def estimate( self ) -> RunEstimate: ...


class IDiskSurveyor( ABC ):
    """Sizes the disk cheaply, with a hard time limit, before anyone scans it."""

    @abstractmethod
    def survey( self ) -> DiskSurvey: ...


class IUserAlert( ABC ):
    @abstractmethod
    def alert( self, title: str, body: str ) -> str:
        """Raise the alert; return a short note on where it went."""


class IUserQuestion( ABC ):
    """A Yes/No question put to a person, waited on."""

    @abstractmethod
    def ask( self, question: str, detail: str, timeout_seconds: float ) -> str:
        """'yes', 'no', or why there is no answer ('expired', 'withdrawn', 'unavailable: ...')."""


class IWorkflowRunner( ABC ):
    """Runs another ADW to completion and returns ( exit code, its last line )."""

    @abstractmethod
    def run( self, args: list[ str ], timeout_seconds: float ) -> tuple[ int, str ]: ...


# -- Adapters ----------------------------------------------------------------

class DashboardPcStatus( LifecycleLogged, IPcStatusSource ):
    """Reads `/api/pc-metrics` -- the endpoint the blinking tab itself polls.

    A stale reading (the dashboard serving its last good sample because the
    collector failed) is not a verdict, so it is retried and then refused
    rather than reported as if it were current.
    """

    def __init__( self, base_url: str, logger: ILifecycleLog, attempts: int = 3 ):
        LifecycleLogged.__init__( self, logger )
        self._base_url = base_url.rstrip( "/" )
        self._attempts = attempts

    def read( self, pc: str ) -> PcStatus:
        problem = ""
        for attempt in range( self._attempts ):
            if attempt:
                time.sleep( 5 )
            try:
                with_url = f"{ self._base_url }/api/pc-metrics?pc={ pc }"
                body = json.loads( urllib.request.urlopen( with_url, timeout=40 ).read())
            except Exception as error:
                problem = f"dashboard unreachable at { self._base_url }: { error }"
                continue
            if not body.get( "ok" ):
                problem = f"dashboard could not sample { pc }: { body.get( 'error', 'no reason given' )}"
                continue
            if body.get( "stale" ):
                problem = f"dashboard's reading for { pc } is stale: { body.get( 'stale_error', '' )}"
                continue
            return PcStatus(
                key=body[ "key" ], label=body[ "label" ], level=body.get( "level", "ok" ),
                metrics=[ MetricReading( key=m[ "key" ], label=m[ "label" ],
                                         level=m.get( "level", "ok" ), text=m[ "text" ])
                          for m in body.get( "metrics", [])])
        raise RuntimeError( problem )


class FirstFoundCommandSource( LifecycleLogged, ICommandSource ):
    """The Desktop copy wins when present; the user-level copy is the fallback."""

    def __init__( self, candidates: tuple[ Path, ... ], logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._candidates = candidates

    def load( self ) -> PcCheckCommand:
        for path in self._candidates:
            if path.is_file():
                text = path.read_text( encoding="utf-8", errors="replace" )
                # A leading YAML frontmatter block is metadata for the slash-command
                # loader, not part of the instructions.
                text = re.sub( r"\A---\n.*?\n---\n", "", text, flags=re.DOTALL )
                return PcCheckCommand( path=str( path ), text=text )
        raise FileNotFoundError( "no pc-check.md in any of: "
                                 + ", ".join( str( p ) for p in self._candidates ))


_JOB_LAUNCHER_PS1 = r"""
param( $session, $model, $tools )
# Put this launcher in a Job Object that kills every member when its last
# handle closes, then start claude from inside it. Job membership is
# inherited by every descendant however it was spawned -- Git Bash's
# fork emulation orphans its children (a dead ParentProcessId), so a
# tree walk misses them and a job does not.
Add-Type -TypeDefinition @"
using System; using System.Runtime.InteropServices;
public static class PcDoctorJob {
  [StructLayout( LayoutKind.Sequential )] struct Basic { public long A; public long B; public uint LimitFlags; public UIntPtr C; public UIntPtr D; public uint E; public UIntPtr F; public uint G; public uint H; }
  [StructLayout( LayoutKind.Sequential )] struct Io { public ulong A, B, C, D, E, F; }
  [StructLayout( LayoutKind.Sequential )] struct Extended { public Basic Basic; public Io Io; public UIntPtr P1, P2, P3, P4; }
  [DllImport( "kernel32.dll" )] static extern IntPtr CreateJobObject( IntPtr attributes, string name );
  [DllImport( "kernel32.dll" )] static extern bool SetInformationJobObject( IntPtr job, int infoClass, ref Extended info, uint length );
  [DllImport( "kernel32.dll" )] static extern bool AssignProcessToJobObject( IntPtr job, IntPtr process );
  [DllImport( "kernel32.dll" )] static extern IntPtr GetCurrentProcess();
  static IntPtr job;
  public static bool Enter() {
    job = CreateJobObject( IntPtr.Zero, null );
    var info = new Extended(); info.Basic.LimitFlags = 0x2000;   // KILL_ON_JOB_CLOSE
    if ( !SetInformationJobObject( job, 9, ref info, ( uint )Marshal.SizeOf( typeof( Extended )))) return false;
    return AssignProcessToJobObject( job, GetCurrentProcess());
  }
}
"@
if ( -not [PcDoctorJob]::Enter()) { [Console]::Error.WriteLine( 'job object unavailable; children may outlive a timeout' ) }
$start = New-Object System.Diagnostics.ProcessStartInfo 'cmd.exe', "/c claude -p --model $model --session-id $session --output-format json --allowedTools $tools"
$start.UseShellExecute = $false    # inherit this launcher's stdin/stdout/stderr as they are
$child = [System.Diagnostics.Process]::Start( $start )
$child.WaitForExit()
exit $child.ExitCode
"""


class WindowsClaudeCli( LifecycleLogged, IClaudeAgent ):
    """Headless Windows-native Claude Code (`claude -p`), prompt on stdin.

    Run through cmd.exe from WSL so the agent's Bash is Git Bash and its paths
    are the `/c/Users/...` ones /pc-check is written for. Only WSLENV-listed
    variables cross into a Windows process, so no ANTHROPIC_API_KEY can leak in
    and switch it off subscription auth.
    """

    def __init__( self, cwd: Path, model: str, tools: str, script_dir: Path, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._script_dir = script_dir
        self._cwd   = cwd
        self._model = model
        self._tools = tools

    def ask( self, prompt: str, timeout_seconds: float, tag: str ) -> AgentReply:
        # Claude wants a fresh UUID per conversation, and one ADW run holds two,
        # so the adw id cannot be used as-is; derived from "<adw_id>:<phase>" it
        # is still traceable to the run. It is also the handle for killing it:
        # it appears on the Windows command line of cmd.exe and claude.exe alike.
        session = str( uuid.uuid5( uuid.NAMESPACE_URL, f"adw_interface_pc_doctor:{ tag }" ))
        launcher = self._script_dir / f"adw_pc_doctor_job_{ session }.ps1"
        launcher.write_text( _JOB_LAUNCHER_PS1, encoding="utf-8" )
        command = [ "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                    "-File", _windows_path( str( launcher )), session, self._model, self._tools ]
        try:
            child = subprocess.Popen( command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                      errors="replace", cwd=self._cwd )
        except OSError as error:
            launcher.unlink( missing_ok=True )
            return AgentReply( ok=False, text="", error=f"could not start claude: { error }" )
        try:
            return self._wait( child, prompt, timeout_seconds, session )
        finally:
            launcher.unlink( missing_ok=True )

    def _wait( self, child: subprocess.Popen, prompt: str, timeout_seconds: float,
               session: str ) -> AgentReply:
        try:
            stdout, stderr = child.communicate( input=prompt, timeout=max( 1, timeout_seconds ))
        except subprocess.TimeoutExpired:
            self._kill_windows_tree( session )
            child.kill()
            try:
                child.communicate( timeout=15 )
            except subprocess.TimeoutExpired:
                pass
            return AgentReply( ok=False, text="", timed_out=True,
                               error=f"stopped after { round( timeout_seconds / 60, 1 )} min (budget)" )
        try:
            envelope = json.loads( stdout )
        except json.JSONDecodeError:
            return AgentReply( ok=False, text=stdout,
                               error=( stderr or "claude printed no JSON envelope" ).strip()[ :400 ])
        return AgentReply( ok=not envelope.get( "is_error", False ),
                           text=envelope.get( "result", "" ) or "",
                           cost_usd=float( envelope.get( "total_cost_usd", 0 ) or 0 ),
                           error=envelope.get( "subtype", "" ) if envelope.get( "is_error" ) else "" )

    @staticmethod
    def _kill_windows_tree( session: str ) -> None:
        """Kill every Windows process whose command line carries `session`, and
        every descendant still linked to one.

        Killing the job launcher is what actually reaps everything: its job
        takes every member down with it, orphans included. The tree walk is the
        fallback for a box where the job could not be created.
        """
        script = (
            "$all = Get-CimInstance Win32_Process; "
            "$queue = New-Object System.Collections.Generic.Queue[uint32]; "
            "$all | Where-Object { $_.CommandLine -like '*" + session + "*' -and $_.ProcessId -ne $PID } | "
            "ForEach-Object { $queue.Enqueue( $_.ProcessId ) }; "
            "$doomed = @{}; "
            "while ( $queue.Count ) { $id = $queue.Dequeue(); if ( $doomed.ContainsKey( $id )) { continue }; "
            "$doomed[ $id ] = 1; "
            "$all | Where-Object { $_.ParentProcessId -eq $id } | ForEach-Object { $queue.Enqueue( $_.ProcessId ) }}; "
            "$doomed.Keys | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }" )
        try:
            subprocess.run( [ "powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script ],
                            capture_output=True, text=True, timeout=60 )
        except ( OSError, subprocess.TimeoutExpired ):
            pass


class PastRunsEstimator( LifecycleLogged, IRunEstimator ):
    """Median phase timings over this ADW's earlier sessions.

    A session counts if it ran `pc_check`. A phase that started and never
    ended -- a run killed by hand -- still counts, timed to that session's
    last event: it is a lower bound, and leaving killed runs out would make
    the estimate most optimistic exactly when it matters.
    """

    def __init__( self, sessions_dir: Path, defaults: dict[ str, float ], logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._sessions_dir = sessions_dir
        self._defaults     = defaults

    def estimate( self ) -> RunEstimate:
        samples: dict[ str, list[ float ]] = {}
        runs = 0
        for events in self._sessions_dir.glob( "*/events.jsonl" ):
            timings = self._timings( events )
            if "pc_check" in timings:
                runs += 1
                for name, seconds in timings.items():
                    samples.setdefault( name, []).append( seconds )
        per_phase = { name: statistics.median( samples[ name ]) if name in samples else default
                      for name, default in self._defaults.items()}
        source = f"median of { runs } earlier run(s)" if runs else "defaults (no earlier runs timed)"
        return RunEstimate( seconds=sum( per_phase.values()), source=source, per_phase=per_phase )

    @staticmethod
    def _timings( events: Path ) -> dict[ str, float ]:
        from datetime import datetime
        starts: dict[ str, tuple[ str, float ]] = {}
        out: dict[ str, float ] = {}
        last = 0.0
        try:
            lines = events.read_text().splitlines()
        except OSError:
            return {}
        for line in lines:
            try:
                event = json.loads( line )
                at = datetime.fromisoformat( event[ "ts" ]).timestamp()
            except ( ValueError, KeyError, TypeError ):
                continue
            last = max( last, at )
            if event.get( "type" ) == "phase_start":
                starts[ event.get( "phase_id", "" )] = ( event.get( "name", "" ), at )
            elif event.get( "type" ) == "phase_end" and event.get( "phase_id", "" ) in starts:
                name, began = starts.pop( event[ "phase_id" ])
                out[ name ] = at - began
        for name, began in starts.values():
            out[ name ] = last - began
        return out


# Folder names whose contents are disposable but which must themselves stay.
CONTAINER_NAMES = frozenset({
    "Temp", "temp", "tmp", "CrashDumps", "WER", "INetCache", "npm-cache", "_cacache",
    "D3DSCache", "SquirrelTemp", "Cache", "Code Cache", "GPUCache", "cache", ".cache",
})
# Folder names that are wholly regenerable from a manifest or a rerun.
DISPOSABLE_NAMES = frozenset({
    "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".next", ".turbo", ".parcel-cache",
})
DISPOSABLE_SUFFIXES = ( ".dmp", ".tmp" )


class RegenerableOnlyPolicy( LifecycleLogged, IDeletionPolicy ):
    """Allows a deletion only if what goes is something a rerun brings back.

    Every path -- the built-in cache list, the stale-env walk, and above all
    whatever the dig_deeper agent proposes -- must be under an allowed root,
    clear of the protected list, free of symlinks, and name a cache, a temp
    folder, a crash dump or a dependency folder somewhere in its own path. A
    `Downloads\\big.iso` fails the last test however large it is, and so turns
    into a recommendation for the user rather than a deletion.
    """

    def __init__( self, allowed_roots: tuple[ Path, ... ], protected: tuple[ Path, ... ],
                  logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._allowed_roots = allowed_roots
        self._protected     = protected
        self._in_use: set[ Path ] | None = None

    def refusal( self, item: CleanupItem ) -> str | None:
        path = Path( item.path )
        if not path.is_absolute():
            return "not an absolute path"
        if not path.exists():
            return "does not exist"
        if os.path.realpath( path ) != str( path ):
            return "is or passes through a symlink/junction"
        root = next(( r for r in self._allowed_roots if path.is_relative_to( r )), None )
        # A root itself may be emptied (C:\Windows\Temp is a root) but never removed.
        if root is None or ( path == root and item.mode == "whole" ):
            return "outside the folders this ADW may clean"
        if any( path.is_relative_to( p ) for p in self._protected ):
            return "inside a protected folder"
        parts = path.relative_to( root ).parts or ( path.name, )
        if ".git" in parts:
            return "inside a git repository's .git"
        if path.name in CONTAINER_NAMES and item.mode == "whole":
            return "would delete a system/cache folder itself rather than its contents"
        regenerable = ( any( p in CONTAINER_NAMES or p in DISPOSABLE_NAMES for p in parts )
                        or path.suffix.lower() in DISPOSABLE_SUFFIXES )
        if not regenerable:
            return "not a cache, temp, crash dump or dependency folder"
        if item.side == "wsl":
            pid = self._user_of( path )
            if pid:
                return f"in use by running process { pid }"
        return None

    def _user_of( self, path: Path ) -> str:
        """A WSL pid whose exe, cwd or command line is under `path`, if any."""
        for proc in Path( "/proc" ).iterdir():
            if not proc.name.isdigit():
                continue
            try:
                cmdline = ( proc / "cmdline" ).read_bytes().replace( b"\0", b" " ).decode( errors="replace" )
                touched = [ os.readlink( proc / "exe" ), os.readlink( proc / "cwd" )]
            except OSError:
                continue
            if str( path ) in cmdline or any( t == str( path ) or t.startswith( f"{ path }/" ) for t in touched ):
                return proc.name
        return ""


class KnownCachePlanner( LifecycleLogged, ICleanupPlanner ):
    """The usual suspects /pc-check itself lists as safe-to-clean, plus the
    package caches on both sides. Temp folders are age-gated so an installer
    or a run that is writing there right now keeps its files."""

    def __init__( self, win_home: Path, wsl_home: Path, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._win_home = win_home
        self._wsl_home = wsl_home

    def plan( self ) -> list[ CleanupItem ]:
        local = self._win_home / "AppData/Local"
        aged = [ local / "Temp", Path( "/mnt/c/Windows/Temp" ), local / "SquirrelTemp" ]
        caches = [
            local / "CrashDumps",
            local / "Microsoft/Windows/INetCache",
            local / "Microsoft/Windows/WER",
            Path( "/mnt/c/ProgramData/Microsoft/Windows/WER" ),
            local / "D3DSCache",
            local / "npm-cache",
            local / "pip/cache",
            local / "uv/cache",
            self._wsl_home / ".cache/pip",
            self._wsl_home / ".cache/uv",
            self._wsl_home / ".npm/_cacache",
        ]
        for browser in ( "Google/Chrome", "Microsoft/Edge", "BraveSoftware/Brave-Browser" ):
            data = local / browser / "User Data"
            if data.is_dir():
                for profile in data.iterdir():
                    if profile.is_dir() and ( profile.name == "Default" or profile.name.startswith( "Profile " )):
                        caches += [ profile / "Cache", profile / "Code Cache", profile / "GPUCache" ]
        items = [ CleanupItem( path=str( p ), mode="contents", min_age_hours=24,
                               reason="temp folder, files older than a day" )
                  for p in aged if p.is_dir() ]
        items += [ CleanupItem( path=str( p ), mode="contents", reason="cache / crash dumps" )
                   for p in caches if p.is_dir() ]
        return items


class StaleEnvironmentPlanner( LifecycleLogged, ICleanupPlanner ):
    """`node_modules` and Python venvs of projects idle for `idle_days`.

    "Unused" is decided from the project, not the environment: the folder
    beside the environment must carry a manifest (so `npm install` / `uv
    sync` can rebuild it), and nothing directly in it -- nor the environment
    itself -- may have changed within the window. A venv with no manifest
    next to it (lancedb_memory's, whose dependencies are declared nowhere) is
    never proposed, because nothing could rebuild it.
    """

    NODE_MANIFESTS   = ( "package.json", )
    PYTHON_MANIFESTS = ( "pyproject.toml", "requirements.txt", "setup.py", "uv.lock", "Pipfile" )

    def __init__( self, roots: tuple[ Path, ... ], idle_days: int, max_depth: int,
                  logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._roots     = roots
        self._idle_days = idle_days
        self._max_depth = max_depth

    def plan( self ) -> list[ CleanupItem ]:
        cutoff = time.time() - self._idle_days * 86400
        found: list[ CleanupItem ] = []
        for root in self._roots:
            if not root.is_dir():
                continue
            base_depth = len( root.parts )
            for here, dirs, _ in os.walk( root ):
                here_path = Path( here )
                if len( here_path.parts ) - base_depth >= self._max_depth:
                    dirs[:] = []
                    continue
                keep = []
                for name in dirs:
                    if name in ( "node_modules", ".venv", "venv" ):
                        item = self._judge( here_path, name, cutoff )
                        if item:
                            found.append( item )
                    elif not name.startswith( "." ) and name not in ( "AppData", "site-packages" ):
                        keep.append( name )
                dirs[:] = keep
        return found

    def _judge( self, project: Path, name: str, cutoff: float ) -> CleanupItem | None:
        env = project / name
        if name == "node_modules":
            manifests = self.NODE_MANIFESTS
        else:
            if not ( env / "pyvenv.cfg" ).is_file():
                return None
            manifests = self.PYTHON_MANIFESTS
        if not any(( project / m ).is_file() for m in manifests ):
            return None
        try:
            newest = max([ env.stat().st_mtime ]
                         + [ e.stat( follow_symlinks=False ).st_mtime
                             for e in project.iterdir() if e.name != name ])
        except OSError:
            return None
        if newest >= cutoff:
            return None
        idle = int(( time.time() - newest ) // 86400 )
        return CleanupItem( path=str( env ), mode="whole",
                            reason=f"{ name } of a project idle { idle } days; rebuildable from its manifest" )


class PosixRemover( LifecycleLogged, IRemover ):
    """WSL-side deletions, in Python."""

    def __init__( self, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )

    def remove( self, items: list[ CleanupItem ], dry_run: bool ) -> list[ CleanupResult ]:
        return [ self._one( item, dry_run ) for item in items ]

    def _one( self, item: CleanupItem, dry_run: bool ) -> CleanupResult:
        path = Path( item.path )
        if item.mode == "whole":
            victims = [ path ]
        else:
            cutoff = time.time() - item.min_age_hours * 3600
            victims = [ c for c in path.iterdir() if c.lstat().st_mtime < cutoff ]
        freed = 0
        for victim in victims:
            size = _tree_bytes( victim )
            if dry_run:
                freed += size
                continue
            try:
                if victim.is_dir() and not victim.is_symlink():
                    shutil.rmtree( victim, ignore_errors=True )
                else:
                    victim.unlink( missing_ok=True )
            except OSError:
                pass
            freed += size - ( _tree_bytes( victim ) if victim.exists() else 0 )
        return CleanupResult( item=item, freed_bytes=freed )


def _tree_bytes( path: Path ) -> int:
    if path.is_symlink() or not path.is_dir():
        try:
            return path.lstat().st_size
        except OSError:
            return 0
    total = 0
    for here, _, files in os.walk( path ):
        for name in files:
            try:
                total += os.lstat( os.path.join( here, name )).st_size
            except OSError:
                pass
    return total


def _windows_path( wsl_path: str ) -> str:
    """/mnt/c/Users/x -> C:\\Users\\x. Only drvfs mounts reach here."""
    match = re.match( r"^/mnt/([a-z])(/.*)?$", wsl_path )
    if not match:
        raise ValueError( f"not a Windows drive path: { wsl_path }" )
    return f"{ match.group( 1 ).upper()}:" + ( match.group( 2 ) or "\\" ).replace( "/", "\\" )


def _wsl_path( given: str ) -> str:
    """Accepts C:\\x, /c/x (Git Bash), \\\\wsl.localhost\\<distro>\\x or a WSL path."""
    text = given.strip().strip( '"' )
    if match := re.match( r"^([A-Za-z]):[\\/](.*)$", text ):
        return f"/mnt/{ match.group( 1 ).lower()}/" + match.group( 2 ).replace( "\\", "/" )
    if match := re.match( r"^/([a-zA-Z])/(.*)$", text ):
        return f"/mnt/{ match.group( 1 ).lower()}/{ match.group( 2 )}"
    if match := re.match( r"^\\\\wsl(?:\.localhost|\$)\\[^\\]+(\\.*)$", text, re.IGNORECASE ):
        return match.group( 1 ).replace( "\\", "/" )
    return text


_WINDOWS_CLEANUP_PS1 = r"""
$ErrorActionPreference = 'SilentlyContinue'
function Measure-Bytes( $p ) {
  if ( -not ( Test-Path -LiteralPath $p )) { return [int64]0 }
  $i = Get-Item -LiteralPath $p -Force
  if ( -not $i.PSIsContainer ) { return [int64]$i.Length }
  $s = ( Get-ChildItem -LiteralPath $p -Recurse -Force -File | Measure-Object -Property Length -Sum ).Sum
  if ( $s ) { [int64]$s } else { [int64]0 }
}
function Remove-Path( $p ) {
  if (( Get-Item -LiteralPath $p -Force ).PSIsContainer ) { & cmd.exe /c rd /s /q $p 2>$null }
  else { Remove-Item -LiteralPath $p -Force }
}
function Clean-Item( $p, $mode, $hours, $dry ) {
  $freed = [int64]0
  if ( $mode -eq 'contents' ) {
    $cut = ( Get-Date ).AddHours( -$hours )
    foreach ( $k in ( Get-ChildItem -LiteralPath $p -Force | Where-Object { $_.LastWriteTime -lt $cut })) {
      $b = Measure-Bytes $k.FullName
      if ( $dry ) { $freed += $b } else { Remove-Path $k.FullName; $freed += $b - ( Measure-Bytes $k.FullName ) }
    }
  } else {
    $b = Measure-Bytes $p
    if ( $dry ) { $freed = $b } else { Remove-Path $p; $freed = $b - ( Measure-Bytes $p ) }
  }
  [pscustomobject]@{ path = $p; freed = $freed } | ConvertTo-Json -Compress
}
"""


class WindowsRemover( LifecycleLogged, IRemover ):
    """Windows-side deletions, done by Windows itself.

    Deleting a node_modules through WSL's drvfs mount is one 9P round trip
    per file, minutes for what `rd /s /q` does in seconds; so the items go
    into one .ps1, run once -- the same "write a script, run the file" rule
    /pc-check sets for itself, for the same quoting reasons. The script is
    written into the very Temp folder being cleaned; being minutes old, the
    24h age gate keeps it from deleting itself.
    """

    def __init__( self, script_dir: Path, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._script_dir = script_dir

    def remove( self, items: list[ CleanupItem ], dry_run: bool ) -> list[ CleanupResult ]:
        if not items:
            return []
        quote = lambda s: "'" + s.replace( "'", "''" ) + "'"
        calls = [ f"Clean-Item { quote( _windows_path( i.path ))} '{ i.mode }' { i.min_age_hours } "
                  f"${ 'true' if dry_run else 'false' }" for i in items ]
        script = self._script_dir / f"adw_pc_doctor_{ os.getpid()}.ps1"
        script.write_text( _WINDOWS_CLEANUP_PS1 + "\n".join( calls ) + "\n", encoding="utf-8" )
        try:
            done = subprocess.run( [ "powershell.exe", "-NoProfile", "-NonInteractive",
                                     "-ExecutionPolicy", "Bypass", "-File", _windows_path( str( script ))],
                                   capture_output=True, text=True, timeout=60 * 60 )
            output, error = done.stdout, ""
        except subprocess.TimeoutExpired as timeout:
            output, error = ( timeout.stdout or b"" ).decode( errors="replace" ), "cleanup script timed out"
        finally:
            script.unlink( missing_ok=True )
        freed: dict[ str, int ] = {}
        for line in output.splitlines():
            line = line.strip()
            if line.startswith( "{" ):
                try:
                    row = json.loads( line )
                    freed[ row[ "path" ].lower()] = int( row.get( "freed" ) or 0 )
                except ( json.JSONDecodeError, KeyError, ValueError ):
                    pass
        return [ CleanupResult( item=i, freed_bytes=max( 0, freed.get( _windows_path( i.path ).lower(), 0 )),
                                error="" if _windows_path( i.path ).lower() in freed else ( error or "no result line" ))
                 for i in items ]


class SideRoutingRemover( LifecycleLogged, IRemover ):
    """Composite: Windows paths to Windows, the rest to Python."""

    def __init__( self, windows: IRemover, wsl: IRemover, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._windows = windows
        self._wsl     = wsl

    def remove( self, items: list[ CleanupItem ], dry_run: bool ) -> list[ CleanupResult ]:
        return ( self._windows.remove([ i for i in items if i.side == "windows" ], dry_run )
                 + self._wsl.remove([ i for i in items if i.side == "wsl" ], dry_run ))


_SURVEY_PS1 = r"""
$ErrorActionPreference = 'SilentlyContinue'
$limit = __LIMIT__; $parallel = __PARALLEL__
$me = $env:USERPROFILE
$plain = { -not ( $_.Attributes -band [IO.FileAttributes]::ReparsePoint ) }
$targets = New-Object System.Collections.Generic.List[string]
Get-ChildItem -LiteralPath 'C:\' -Force -Directory | Where-Object $plain | Where-Object { $_.Name -ne 'Users' } | ForEach-Object { $targets.Add( $_.FullName ) }
Get-ChildItem -LiteralPath 'C:\Users' -Force -Directory | Where-Object $plain | Where-Object { $_.FullName -ne $me } | ForEach-Object { $targets.Add( $_.FullName ) }
Get-ChildItem -LiteralPath $me -Force -Directory | Where-Object $plain | Where-Object { $_.Name -ne 'AppData' } | ForEach-Object { $targets.Add( $_.FullName ) }
Get-ChildItem -LiteralPath "$me\AppData\Local" -Force -Directory | Where-Object $plain | ForEach-Object { $targets.Add( $_.FullName ) }
foreach ( $sub in 'Roaming', 'LocalLow' ) { if ( Test-Path -LiteralPath "$me\AppData\$sub" ) { $targets.Add( "$me\AppData\$sub" ) } }
# The big system files, which no folder listing includes.
Get-ChildItem -LiteralPath 'C:\' -Force -File | Where-Object { $_.Name -in 'hiberfil.sys', 'pagefile.sys', 'swapfile.sys' } | ForEach-Object {
  [pscustomobject]@{ path = $_.FullName; dirs = 0; files = 1; bytes = [int64]$_.Length; complete = $true; seconds = 0 } | ConvertTo-Json -Compress }
$running = New-Object System.Collections.ArrayList
$next = 0
while ( $next -lt $targets.Count -or $running.Count ) {
  while ( $running.Count -lt $parallel -and $next -lt $targets.Count ) {
    $target = $targets[ $next ]; $next++
    $out = Join-Path $env:TEMP ( 'adw_pc_survey_' + [guid]::NewGuid() + '.txt' )
    $proc = Start-Process robocopy.exe -ArgumentList "`"$target`" C:\nul /L /S /NJH /NFL /NDL /BYTES /XJ /R:0 /W:0 /MT:8" `
              -RedirectStandardOutput $out -NoNewWindow -PassThru
    [void]$running.Add( @{ proc = $proc; path = $target; out = $out; start = Get-Date } )
  }
  Start-Sleep -Milliseconds 200
  foreach ( $job in @( $running )) {
    $age = (( Get-Date ) - $job.start ).TotalSeconds
    if ( -not $job.proc.HasExited -and $age -lt $limit ) { continue }
    $complete = $job.proc.HasExited
    if ( -not $complete ) { Stop-Process -Id $job.proc.Id -Force }
    $text = Get-Content -LiteralPath $job.out -Raw
    $count = { param( $label ) if ( $text -match "(?m)^\s*$label\s*:\s*(\d+)" ) { [int64]$Matches[ 1 ] } else { [int64]0 } }
    [pscustomobject]@{ path = $job.path; dirs = & $count 'Dirs'; files = & $count 'Files'; bytes = & $count 'Bytes'
                       complete = $complete; seconds = [math]::Round( $age, 1 ) } | ConvertTo-Json -Compress
    Remove-Item -LiteralPath $job.out -Force
    $running.Remove( $job )
  }
}
# A killed robocopy can still hold its output file for a moment.
Start-Sleep -Seconds 1
Remove-Item -Path ( Join-Path $env:TEMP 'adw_pc_survey_*.txt' ) -Force
"""


class RobocopySurveyor( LifecycleLogged, IDiskSurveyor ):
    """Counts with `robocopy /L`, from one self-limiting PowerShell script.

    The time limit is enforced inside Windows, per folder, so the survey
    cannot run away even if this side never times it out; and robocopy does
    not spawn children for a dead parent link to orphan.
    """

    def __init__( self, script_dir: Path, folder_seconds: int, parallel: int, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._script_dir     = script_dir
        self._folder_seconds = folder_seconds
        self._parallel       = parallel

    def survey( self ) -> DiskSurvey:
        began = time.monotonic()
        script = self._script_dir / f"adw_pc_doctor_survey_{ os.getpid()}.ps1"
        script.write_text( _SURVEY_PS1.replace( "__LIMIT__", str( self._folder_seconds ))
                                      .replace( "__PARALLEL__", str( self._parallel )), encoding="utf-8" )
        try:
            done = subprocess.run( [ "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                                     "Bypass", "-File", _windows_path( str( script ))],
                                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   timeout=15 * 60 )
            output = done.stdout
        except subprocess.TimeoutExpired as timeout:
            output = ( timeout.stdout or b"" ).decode( errors="replace" ) if isinstance( timeout.stdout, bytes ) else ( timeout.stdout or "" )
        finally:
            script.unlink( missing_ok=True )
        entries = []
        for line in output.splitlines():
            line = line.strip()
            if line.startswith( "{" ):
                try:
                    entries.append( SurveyEntry.model_validate_json( line ))
                except ValueError:
                    pass
        disk = shutil.disk_usage( "/mnt/c" )
        return DiskSurvey( entries=entries, c_total=disk.total, c_free=disk.free,
                           seconds=time.monotonic() - began )


_TOAST_PS1 = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent( [Windows.UI.Notifications.ToastTemplateType]::ToastText02 )
$x = $t.GetElementsByTagName( 'text' )
$x.Item( 0 ).AppendChild( $t.CreateTextNode( __TITLE__ )) > $null
$x.Item( 1 ).AppendChild( $t.CreateTextNode( __BODY__ )) > $null
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier( $app ).Show( [Windows.UI.Notifications.ToastNotification]::new( $t ))
"""


class WindowsToastAlert( LifecycleLogged, IUserAlert ):
    """A desktop toast on this box. Never raises: a failed toast is a note."""

    def __init__( self, script_dir: Path, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._script_dir = script_dir

    def alert( self, title: str, body: str ) -> str:
        quote = lambda s: "'" + s.replace( "'", "''" ) + "'"
        script = self._script_dir / f"adw_pc_doctor_toast_{ os.getpid()}.ps1"
        script.write_text( _TOAST_PS1.replace( "__TITLE__", quote( title ))
                                     .replace( "__BODY__", quote( body[ :250 ])), encoding="utf-8" )
        problem = ""
        try:
            # Twice: WSL interop now and then drops one launch (UtilAcceptVsock).
            for _ in range( 2 ):
                try:
                    done = subprocess.run( [ "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                                             "Bypass", "-File", _windows_path( str( script ))],
                                           capture_output=True, text=True, timeout=60 )
                except ( OSError, subprocess.TimeoutExpired ) as error:
                    problem = str( error )
                    continue
                if done.returncode == 0:
                    return "Windows toast shown"
                problem = done.stderr.strip()[ :200 ]
            return f"toast failed: { problem }"
        finally:
            script.unlink( missing_ok=True )


class FileAlert( LifecycleLogged, IUserAlert ):
    """ALERT.md in the run's session folder: the full text a toast can't hold."""

    def __init__( self, folder: Path, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._folder = folder

    def alert( self, title: str, body: str ) -> str:
        path = self._folder / "ALERT.md"
        path.write_text( f"# { title }\n\n{ body }\n", encoding="utf-8" )
        return f"written to { path }"


class AllAlerts( IUserAlert ):
    """Composite: every channel, so one failing never silences the others."""

    def __init__( self, *channels: IUserAlert ):
        self._channels = channels

    def alert( self, title: str, body: str ) -> str:
        return "; ".join( c.alert( title, body ) for c in self._channels )


class DashboardQuestion( LifecycleLogged, IUserQuestion ):
    """Raises the dashboard's Yes/No modal (/api/adw-prompt) and polls for the
    click. The dashboard only records it; acting on it is this ADW's job. On
    timeout the question is withdrawn, so a late click can't count."""

    def __init__( self, base_url: str, source: str, logger: ILifecycleLog, poll_seconds: float = 3 ):
        LifecycleLogged.__init__( self, logger )
        self._base_url = base_url.rstrip( "/" )
        self._source   = source
        self._poll     = poll_seconds

    def _call( self, path: str, payload: dict | None = None ) -> dict:
        data = json.dumps( payload ).encode() if payload is not None else None
        req = urllib.request.Request( f"{ self._base_url }{ path }", data=data,
                                      headers={ "Content-Type": "application/json" })
        return json.loads( urllib.request.urlopen( req, timeout=20 ).read())

    def ask( self, question: str, detail: str, timeout_seconds: float ) -> str:
        try:
            created = self._call( "/api/adw-prompt", { "question": question, "detail": detail, "source": self._source,
                                                       "expires_in_seconds": timeout_seconds + 60 })
            prompt_id = created[ "prompt" ][ "id" ]
        except Exception as error:
            return f"unavailable: { error }"
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            time.sleep( self._poll )
            try:
                status = self._call( f"/api/adw-prompt?id={ prompt_id }" )[ "prompt" ][ "status" ]
            except Exception:
                continue          # the dashboard blinked; the question is still there
            if status != "pending":
                return status
        try:
            self._call( "/api/adw-prompt-withdraw", { "id": prompt_id })
        except Exception:
            pass                  # it expires on its own a minute later anyway
        return "expired"


class UvWorkflowRunner( LifecycleLogged, IWorkflowRunner ):
    """`uv run <script> ...` from the repo root, the way every ADW is run."""

    def __init__( self, script: Path, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._script = script

    def run( self, args: list[ str ], timeout_seconds: float ) -> tuple[ int, str ]:
        line_file = Path( f"/tmp/adw_pc_doctor_sub_{ os.getpid()}.txt" )
        try:
            done = subprocess.run( [ "uv", "run", str( self._script ), *args, "--message-out", str( line_file )],
                                   capture_output=True, text=True, timeout=timeout_seconds,
                                   cwd=self._script.parent.parent )
        except ( OSError, subprocess.TimeoutExpired ) as error:
            return 1, f"{ self._script.name } did not finish: { error }"
        line = line_file.read_text().strip() if line_file.exists() else ( done.stderr or done.stdout ).strip()[ -300: ]
        line_file.unlink( missing_ok=True )
        return done.returncode, line


# -- Phases ------------------------------------------------------------------

class RequestCapturePhase( LifecycleLogged, IPhase ):
    """Records which box is being treated and how, nothing more."""

    def __init__( self, engineer: str, request: PcDoctorRequest, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._engineer = engineer
        self._request  = request

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="request", kind="engineer", owner=self._engineer,
                            description="Record which PC Monitor box is being treated and under what options" )

    def act( self, handle: IPhaseHandle ) -> None:
        r = self._request
        handle.log( input=r.prompt, pc=r.pc, dry_run=r.dry_run, no_agent=r.no_agent, idle_days=r.idle_days )


class ReadStatusPhase( LifecycleLogged, IPhase ):
    """Asks the dashboard for the colour of this box's tab."""

    def __init__( self, name: str, why: str, source: IPcStatusSource, pc: str, wait_seconds: int,
                  logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._name   = name
        self._why    = why
        self._source = source
        self._pc     = pc
        self._wait   = wait_seconds

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name=self._name, kind="code", owner="dashboard", description=self._why )

    def act( self, handle: IPhaseHandle ) -> PcStatus:
        if self._wait:
            time.sleep( self._wait )
        status = self._source.read( self._pc )
        handle.log( level=status.level, status=status.describe())
        return status


class SurveyPhase( LifecycleLogged, IPhase ):
    """Maps the disk before any agent is let loose on it."""

    def __init__( self, surveyor: IDiskSurveyor, map_path: Path, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._surveyor = surveyor
        self._map_path = map_path

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="survey", kind="code", owner="pc_doctor",
                            description="Size every top-level folder cheaply with robocopy list mode, time-capped, so the agents know what is big before they scan anything" )

    def act( self, handle: IPhaseHandle ) -> DiskSurvey:
        survey = self._surveyor.survey()
        self._map_path.write_text( survey.to_prompt( top=10_000 ) + "\n", encoding="utf-8" )
        biggest = max(( e for e in survey.entries if e.complete ), key=lambda e: e.bytes, default=None )
        handle.log( folders=len( survey.entries ), seconds=round( survey.seconds ),
                    too_big_to_scan=len( survey.too_big ), map=str( self._map_path ),
                    biggest=f"{ biggest.path } { _gb( biggest.bytes )}" if biggest else "(none)" )
        return survey


class EstimatePhase( LifecycleLogged, IPhase ):
    """Says up front how long this is going to take, and toasts if too long."""

    def __init__( self, estimator: IRunEstimator, budget: RunBudget, alert: IUserAlert,
                  label: str, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._estimator = estimator
        self._budget    = budget
        self._alert     = alert
        self._label     = label

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="estimate", kind="code", owner="pc_doctor",
                            description="Estimate the whole run from earlier runs' timings and warn the user up front if it exceeds the budget" )

    def act( self, handle: IPhaseHandle ) -> RunEstimate:
        estimate = self._estimator.estimate()
        handle.log( estimated_minutes=estimate.minutes, budget_minutes=self._budget.minutes,
                    source=estimate.source )
        if estimate.minutes > self._budget.minutes:
            where = self._alert.alert(
                f"PC Doctor: ~{ estimate.minutes } min for { self._label }",
                f"Estimated { estimate.minutes } min ({ estimate.source }), over the "
                f"{ self._budget.minutes }-min budget. It will stop at { self._budget.minutes } min "
                f"and tell you what it found." )
            handle.log( alerted=where )
        return estimate


class PcCheckPhase( LifecycleLogged, IPhase ):
    """Sonnet runs /pc-check, read-only, and its report is kept."""

    ADDENDUM = """

---
You are running unattended, launched by adw_interface_pc_doctor.py because the
dashboard's PC Monitor shows this machine as: {status}

Nobody is at the keyboard. Do not ask whether to proceed, and do not delete,
kill, uninstall or change anything: a separate, code-driven step does the
cleaning after you, from your report. Finish with the report itself.
You have about {minutes} minutes before you are stopped.

{rules}"""

    def __init__( self, agent: IClaudeAgent, commands: ICommandSource, status: PcStatus,
                  survey: DiskSurvey, report_path: Path, timeout_seconds: float, tag: str,
                  logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._survey      = survey
        self._tag         = tag
        self._timeout     = timeout_seconds
        self._agent       = agent
        self._commands    = commands
        self._status      = status
        self._report_path = report_path

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="pc_check", kind="agent", owner="claude-sonnet",
                            description="Have Sonnet run the /pc-check command read-only and keep its report" )

    def act( self, handle: IPhaseHandle ) -> AgentReply:
        command = self._commands.load()
        handle.log( command=command.path, timeout_minutes=round( self._timeout / 60, 1 ))
        addendum = self.ADDENDUM.format(
            status=self._status.describe(), minutes=int( self._timeout // 60 ),
            rules=SCAN_RULES.format( limit=self._survey.file_limit, map=self._survey.to_prompt()))
        reply = self._agent.ask( command.text + addendum, self._timeout, self._tag )
        self._report_path.write_text( reply.text or f"(no report: { reply.error })", encoding="utf-8" )
        handle.log( ok=reply.ok, cost_usd=round( reply.cost_usd, 4 ), report=str( self._report_path ),
                    timed_out=reply.timed_out, error=reply.error or "(none)" )
        return reply


class PlanCleanupPhase( LifecycleLogged, IPhase ):
    """Collects candidates from every planner and lets the policy sort them."""

    def __init__( self, name: str, why: str, planners: list[ ICleanupPlanner ],
                  extra: list[ CleanupItem ], policy: IDeletionPolicy, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._name     = name
        self._why      = why
        self._planners = planners
        self._extra    = extra
        self._policy   = policy

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name=self._name, kind="code", owner="pc_doctor", description=self._why )

    def act( self, handle: IPhaseHandle ) -> tuple[ list[ CleanupItem ], list[ str ]]:
        candidates = [ item for planner in self._planners for item in planner.plan()] + self._extra
        approved, refused = [], []
        for item in candidates:
            reason = self._policy.refusal( item )
            if reason:
                refused.append( f"{ item.path }: { reason }" )
            else:
                approved.append( item )
        handle.log( candidates=len( candidates ), approved=len( approved ), refused=len( refused ))
        for line in refused:
            handle.log( refused=line )
        return approved, refused


class CleanupPhase( LifecycleLogged, IPhase ):
    """Deletes approved items and measures both disks either side of it."""

    def __init__( self, name: str, why: str, remover: IRemover, items: list[ CleanupItem ],
                  refused: list[ str ], dry_run: bool, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._name    = name
        self._why     = why
        self._remover = remover
        self._items   = items
        self._refused = refused
        self._dry_run = dry_run

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name=self._name, kind="code", owner="pc_doctor", description=self._why )

    def act( self, handle: IPhaseHandle ) -> CleanupReport:
        c_before, wsl_before = shutil.disk_usage( "/mnt/c" ).free, shutil.disk_usage( "/" ).free
        if not self._dry_run and self._items:
            # Look first: size every approved item and say what is about to go,
            # so the trace shows the plan even if the deletion is interrupted.
            preview = self._remover.remove( self._items, True )
            handle.log( about_to_free=_gb( sum( r.freed_bytes for r in preview )), items=len( self._items ))
            for r in sorted( preview, key=lambda r: r.freed_bytes, reverse=True )[ :10 ]:
                handle.log( planned=f"{ _gb( r.freed_bytes ):>9}  { r.item.path }" )
        results = self._remover.remove( self._items, self._dry_run )
        report = CleanupReport( results=results, refused=self._refused, dry_run=self._dry_run,
                                c_free_before=c_before, c_free_after=shutil.disk_usage( "/mnt/c" ).free,
                                wsl_free_before=wsl_before, wsl_free_after=shutil.disk_usage( "/" ).free )
        handle.log( windows_freed=_gb( report.freed( "windows" )), wsl_freed=_gb( report.freed( "wsl" )),
                    c_free=f"{ _gb( c_before )} -> { _gb( report.c_free_after )}" )
        for result in results:
            if result.error:
                handle.log( item_error=f"{ result.item.path }: { result.error }" )
        return report


class DigDeeperPhase( LifecycleLogged, IPhase ):
    """Sonnet looks again, knowing what was tried, and proposes -- only proposes."""

    PROMPT = """The dashboard's PC Monitor still shows this Windows machine as not healthy
after a first health check and an automatic cleanup. Dig deeper and find out
what is really using the resource that is alarming.

Current dashboard reading:
{status}

Dashboard thresholds: RAM alerts at 90% used; C: drive is yellow under 5 GB
free and red at 2 GB free or less. The C: drive is what is measured.

What the automatic cleanup already did:
{cleanup}

The first /pc-check report:
<<<
{report}
>>>

{rules}
You have about {minutes} minutes before you are stopped.

Investigate with read-only commands, keeping to the rules above.
Look beyond the usual temp folders: WSL and Docker virtual disks (.vhdx),
hiberfil.sys and pagefile.sys, Windows.old, the Recycle Bin, Downloads,
large media or installers, old Windows Update payloads, shadow copies,
duplicate SDKs/toolchains, and for RAM: which processes, and whether they are
leaking. Do NOT delete, kill, uninstall or change anything yourself.

A code step after you will delete only what you list under "deletions", and
only if it is regenerable: a cache, temp or crash-dump folder's contents, or a
node_modules / .venv / __pycache__ folder. Anything else, however large,
belongs under "recommendations" for the user to decide -- with its size and
the exact action you suggest.

Write your findings for a human first, then end your reply with exactly one
fenced json block of this shape (Windows paths are fine):

```json
{{"summary": "one paragraph", "deletions": [{{"path": "C:\\\\...", "reason": "..."}}],
  "recommendations": ["... (size) -- suggested action"]}}
```
"""

    def __init__( self, agent: IClaudeAgent, status: PcStatus, cleanup: CleanupReport,
                  first_report: str, survey: DiskSurvey, report_path: Path, timeout_seconds: float,
                  tag: str, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._survey       = survey
        self._tag          = tag
        self._timeout      = timeout_seconds
        self._agent        = agent
        self._status       = status
        self._cleanup      = cleanup
        self._first_report = first_report
        self._report_path  = report_path

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="dig_deeper", kind="agent", owner="claude-sonnet",
                            description="Have Sonnet investigate what the first check and cleanup missed, proposing deletions rather than making them" )

    def act( self, handle: IPhaseHandle ) -> DeeperFindings:
        reply = self._agent.ask( self.PROMPT.format(
            status=self._status.describe(), cleanup=self._cleanup.summary(),
            report=self._first_report[ -12000: ] or "(the first check produced no report)",
            rules=SCAN_RULES.format( limit=self._survey.file_limit, map=self._survey.to_prompt()),
            minutes=int( self._timeout // 60 )),
            self._timeout, self._tag )
        self._report_path.write_text( reply.text or f"(no report: { reply.error })", encoding="utf-8" )
        findings = self._parse( reply.text )
        handle.log( ok=reply.ok, cost_usd=round( reply.cost_usd, 4 ), report=str( self._report_path ),
                    timed_out=reply.timed_out, timeout_minutes=round( self._timeout / 60, 1 ),
                    proposed_deletions=len( findings.deletions ),
                    recommendations=len( findings.recommendations ))
        return findings

    @staticmethod
    def _parse( text: str ) -> DeeperFindings:
        blocks = re.findall( r"```json\s*(\{.*?\})\s*```", text or "", flags=re.DOTALL )
        for block in reversed( blocks ):
            try:
                return DeeperFindings.model_validate_json( block )
            except ValueError:
                continue
        # No parseable block: the prose still exists in the report file, and the
        # towel alert points there; nothing gets deleted on an unparsed reply.
        return DeeperFindings( summary=( text or "" )[ -1500: ])


class AlertUserPhase( LifecycleLogged, IPhase ):
    """Throws in the towel, out loud."""

    def __init__( self, alert: IUserAlert, status: PcStatus, findings: DeeperFindings | None,
                  reports: list[ Path ], note: str, next_step: str, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._note     = note
        self._next     = next_step
        self._alert    = alert
        self._status   = status
        self._findings = findings
        self._reports  = reports

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="alert_user", kind="code", owner="pc_doctor",
                            description="Tell the user the automatic fixes did not clear the alarm and what drastic step is left" )

    def act( self, handle: IPhaseHandle ) -> str:
        title = f"{ self._status.label } still { self._status.level.upper()} -- needs you"
        lines = [ self._status.describe(), "",
                  self._note or "Automatic cleanup and a deeper check did not clear it. Time for a drastic measure." ]
        if self._findings and self._findings.summary:
            lines += [ "", self._findings.summary ]
        if self._findings and self._findings.recommendations:
            lines += [ "", "Recommended:" ] + [ f"- { r }" for r in self._findings.recommendations ]
        lines += [ "", "Reports:" ] + [ f"- { p }" for p in self._reports ]
        if self._next:
            lines = [ self._next, "" ] + lines
        where = self._alert.alert( title, "\n".join( lines ))
        handle.log( alerted=where )
        return title


class AskCompactPhase( LifecycleLogged, IPhase ):
    """Puts "Run WSL Compact?" on the dashboard and waits for Yes or No."""

    def __init__( self, question: IUserQuestion, status: PcStatus, timeout_seconds: float, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._question = question
        self._status   = status
        self._timeout  = timeout_seconds

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="ask_compact", kind="code", owner="dashboard",
                            description="Ask on the dashboard whether to shut WSL down and compact its disk" )

    def act( self, handle: IPhaseHandle ) -> str:
        detail = ( f"{ self._status.describe()}. Cleanup did not clear it. Yes shuts { self._status.label }'s WSL "
                   "down -- Letta, Docker, MySQL and every Claude session in it stop -- while its disk file is "
                   f"compacted, after a UAC prompt on { self._status.label }'s screen; they come back when it is "
                   "done. No ends the run." )
        answer = self._question.ask( COMPACT_QUESTION, detail, self._timeout )
        handle.log( question=COMPACT_QUESTION, answer=answer, waited_minutes=round( self._timeout / 60, 1 ))
        return answer


class RunCompactPhase( LifecycleLogged, IPhase ):
    """Yes: hands the box to adw_interface_wsl_compact.py. It returns as soon
    as its Windows-side worker is staged; WSL goes down after this run ends."""

    def __init__( self, runner: IWorkflowRunner, adw_id: str, status: PcStatus, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._runner = runner
        self._adw_id = adw_id
        self._status = status

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="run_compact", kind="code", owner="wsl_compact",
                            description="Run the WSL compaction ADW the user said Yes to" )

    def act( self, handle: IPhaseHandle ) -> tuple[ int, str ]:
        code, line = self._runner.run( [ f"pc_doctor { self._adw_id }: { self._status.describe()}",
                                         "--from-adw", self._adw_id ], 20 * 60 )
        handle.log( exit_code=code, result=line )
        return code, line


# -- Argument parsing --------------------------------------------------------

class ArgparsePcDoctorParser( LifecycleLogged, IArgumentParser ):
    """The only class allowed to know this CLI is built on argparse."""

    def __init__( self, description: str, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._description = description

    def parse( self, argv: list[ str ]) -> PcDoctorRequest:
        parser = argparse.ArgumentParser( description=self._description,
                                          formatter_class=argparse.RawDescriptionHelpFormatter )
        parser.add_argument( "prompt", nargs="?", default="PC Monitor tab is blinking",
                             help="inline text or a path to a prompt file; why this is being run" )
        parser.add_argument( "--pc", default="", choices=[ "", *MONITOR_FOR_HOST.values()],
                             help="dashboard monitor key; defaults to this box's own" )
        parser.add_argument( "--dashboard-url", default=DEFAULT_DASHBOARD_URL )
        parser.add_argument( "--dry-run", action="store_true",
                             help="measure what would be deleted, delete nothing, stop after cleanup" )
        parser.add_argument( "--no-agent", action="store_true",
                             help="skip both Sonnet phases: cleanup, recheck, alert" )
        parser.add_argument( "--even-if-ok", action="store_true",
                             help="treat the box even if the dashboard says it is healthy" )
        parser.add_argument( "--idle-days", type=int, default=45,
                             help="a project untouched this long has an 'unused' node_modules/.venv" )
        parser.add_argument( "--budget-minutes", type=int, default=DEFAULT_BUDGET_MINUTES,
                             help="hard deadline for the whole run; agents are stopped to meet it" )
        parser.add_argument( "--no-compact-offer", action="store_true",
                             help="still blinking at the end: alert only, don't ask 'Run WSL Compact?'" )
        parser.add_argument( "--answer-minutes", type=int, default=DEFAULT_ANSWER_MINUTES,
                             help="how long the dashboard question waits before it counts as No" )
        parser.add_argument( "--message-out", default="",
                             help="write the operator's one line here, for a non-terminal caller" )
        parser.add_argument( "--config", default="adws/adw_sssf_config/sssf.config.yaml" )
        parser.add_argument( "--adw-id", default=None, help="join or pin an existing session" )
        parsed = parser.parse_args( argv )

        here = MONITOR_FOR_HOST.get( socket.gethostname().upper(), "" )
        pc = parsed.pc or here
        if not pc:
            parser.error( f"this box ({ socket.gethostname()}) is not on the PC Monitor tab; "
                          f"known: { MONITOR_FOR_HOST }" )
        if pc != here:
            parser.error( f"--pc { pc } is another box; run this ADW there -- it deletes local files" )
        return PcDoctorRequest( prompt=utils.resolve_prompt( parsed.prompt ), pc=pc,
                                dashboard_url=parsed.dashboard_url, dry_run=parsed.dry_run,
                                no_agent=parsed.no_agent, even_if_ok=parsed.even_if_ok,
                                idle_days=parsed.idle_days, budget_minutes=parsed.budget_minutes,
                                offer_compact=not parsed.no_compact_offer, answer_minutes=parsed.answer_minutes,
                                message_out=parsed.message_out,
                                config=parsed.config, adw_id=parsed.adw_id )


# -- Composition root's one orchestrator -------------------------------------

class PcDoctorApplication( LifecycleLogged, IApplication ):
    """Climbs the escalation ladder, stopping at the first rung that clears
    the alarm. Depends on roles, plus the concrete `Run` every phase shares
    (the same exception the shipped template makes)."""

    def __init__( self,
                  argument_parser: IArgumentParser,
                  config_loader:   IConfigLoader,
                  session_factory: ISessionFactory,
                  phase_executor:  IPhaseExecutor,
                  status_factory:  object,
                  agent:           IClaudeAgent,
                  commands:        ICommandSource,
                  planners_factory: object,
                  policy:          IDeletionPolicy,
                  remover:         IRemover,
                  alert_factory:   object,
                  estimator_factory: object,
                  heads_up:        IUserAlert,
                  surveyor:        IDiskSurveyor,
                  question_factory: object,
                  compactor:       IWorkflowRunner,
                  logger:          ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._argument_parser  = argument_parser
        self._config_loader    = config_loader
        self._session_factory  = session_factory
        self._phase_executor   = phase_executor
        self._status_factory   = status_factory
        self._agent            = agent
        self._commands         = commands
        self._planners_factory = planners_factory
        self._policy           = policy
        self._remover          = remover
        self._alert_factory    = alert_factory
        self._estimator_factory = estimator_factory
        self._heads_up          = heads_up
        self._surveyor          = surveyor
        self._question_factory  = question_factory
        self._compactor         = compactor

    def run( self, argv: list[ str ]) -> int:
        request = self._argument_parser.parse( argv )
        cfg     = self._config_loader.load( request.config )
        self._config_loader.validate( cfg, REQUIRED_AGENTS )
        run = self._session_factory.create( cfg, request.adw_id )
        log: ILifecycleLog = ConsoleLifecycleLog( run )
        execute = lambda phase: self._phase_executor.execute( run, phase )
        source: IPcStatusSource = self._status_factory( request.dashboard_url )

        budget = RunBudget( request.budget_minutes )
        execute( RequestCapturePhase( run.engineer, request, log ))
        status = execute( ReadStatusPhase( "status_before", "Read the dashboard's verdict on this box before touching anything",
                                           source, request.pc, 0, log ))
        if status.healthy and not request.even_if_ok:
            return self._finish( run, request, True, f"{ status.describe()} -- nothing to treat." )

        alert = self._alert_factory( run.session_dir )
        survey = DiskSurvey()
        if not request.no_agent:
            survey = execute( SurveyPhase( self._surveyor, run.session_dir / "disk_map.md", log ))
            # Toast only: ALERT.md is the towel's, and a run that clears must not leave one.
            execute( EstimatePhase( self._estimator_factory( run.session_dir.parent ), budget, self._heads_up,
                                    status.label, log ))

        check_path = run.session_dir / "pc_check_report.md"
        check_text = ""
        if not request.no_agent:
            slice_seconds = min( budget.remaining() * PC_CHECK_BUDGET_SHARE,
                                 budget.remaining() - CODE_RESERVE_SECONDS )
            check = execute( PcCheckPhase( self._agent, self._commands, status, survey, check_path,
                                           slice_seconds, f"{ run.adw_id }:pc_check", log ))
            check_text = check.text

        approved, refused = execute( PlanCleanupPhase(
            "plan_cleanup", "Gather temp, cache and stale-environment candidates and let the deletion policy rule on each",
            self._planners_factory( request ), [], self._policy, log ))
        cleanup = execute( CleanupPhase(
            "cleanup", "Delete the approved temp, cache and stale-environment items and measure what came back",
            self._remover, approved, refused, request.dry_run, log ))
        if request.dry_run:
            return self._finish( run, request, True, f"Dry run. { status.describe()}.\n{ cleanup.summary()}" )

        status = execute( ReadStatusPhase( "status_after_cleanup", "Ask the dashboard whether the cleanup cleared the alarm",
                                           source, request.pc, DASHBOARD_CACHE_WAIT_SECONDS, log ))
        if status.healthy:
            return self._finish( run, request, True, f"Cleared by cleanup. { status.describe()}.\n{ cleanup.summary()}" )

        findings: DeeperFindings | None = None
        note = ""
        reports = [ p for p in ( run.session_dir / "disk_map.md", check_path ) if p.exists()]
        dig_seconds = budget.remaining() - CODE_RESERVE_SECONDS
        if not request.no_agent and dig_seconds < 60:
            note = ( f"The { budget.minutes }-min budget ran out before the deeper check could run. "
                     "Rerun with a larger --budget-minutes, or take a drastic measure." )
        elif not request.no_agent:
            dig_path = run.session_dir / "dig_deeper_report.md"
            findings = execute( DigDeeperPhase( self._agent, status, cleanup, check_text, survey, dig_path,
                                                dig_seconds, f"{ run.adw_id }:dig_deeper", log ))
            reports.append( dig_path )
            proposals = [ self._as_item( d ) for d in findings.deletions ]
            approved, refused = execute( PlanCleanupPhase(
                "vet_proposals", "Put every deletion the agent proposed through the same regenerable-only policy",
                [], proposals, self._policy, log ))
            # A refused proposal is not dropped: it is exactly the kind of thing
            # the user has to decide on, so it joins the recommendations.
            findings.recommendations += [ f"Not deleted automatically -- { r }" for r in refused ]
            execute( CleanupPhase( "apply_proposals", "Delete the agent's proposals that the policy approved",
                                   self._remover, approved, refused, False, log ))
            status = execute( ReadStatusPhase( "status_after_dig", "Ask the dashboard whether digging deeper cleared the alarm",
                                               source, request.pc, DASHBOARD_CACHE_WAIT_SECONDS, log ))
            if status.healthy:
                return self._finish( run, request, True, f"Cleared after digging deeper. { status.describe()}." )

        asking = request.offer_compact and COMPACT_ADW.is_file()
        next_step = f"The dashboard is asking: \"{ COMPACT_QUESTION }\"" if asking else ""
        title = execute( AlertUserPhase( alert, status, findings, reports, note, next_step, log ))
        towel = f"{ title }. { status.describe()}. See { run.session_dir / 'ALERT.md' }"
        if not asking:
            return self._finish( run, request, False, towel )

        answer = execute( AskCompactPhase( self._question_factory( request.dashboard_url ), status,
                                           request.answer_minutes * 60, log ))
        if answer != "yes":
            # No (or no answer): abort and exit, nothing else to do.
            return self._finish( run, request, False, f"{ towel }. { COMPACT_QUESTION } -> { answer }." )
        code, line = execute( RunCompactPhase( self._compactor, run.adw_id, status, log ))
        # Finishing fast matters: the compaction's worker shuts WSL down -- this
        # process with it -- a grace period after it was released.
        return self._finish( run, request, False, f"{ towel }. { COMPACT_QUESTION } -> yes. { line }" )

    @staticmethod
    def _as_item( proposal: ProposedDeletion ) -> CleanupItem:
        path = _wsl_path( proposal.path ).rstrip( "/" )
        # A proposal to delete a Temp/cache folder itself is taken as "empty it".
        mode: Mode = "contents" if Path( path ).name in CONTAINER_NAMES else "whole"
        return CleanupItem( path=path, mode=mode, min_age_hours=24 if mode == "contents" else 0,
                            reason=f"proposed by dig_deeper: { proposal.reason }" )

    @staticmethod
    def _finish( run: Run, request: PcDoctorRequest, accepted: bool, message: str ) -> int:
        print( message )
        if request.message_out:
            Path( request.message_out ).write_text( message + "\n" )
        return run.finish( accepted=accepted, reason=message.splitlines()[ 0 ])


def build_application() -> IApplication:
    """Wire the concrete adapters into the application. The one spot that may."""
    bootstrap_log: ILifecycleLog = PrintLifecycleLog()
    windows_temp = WIN_HOME / "AppData/Local/Temp"
    return PcDoctorApplication(
        argument_parser  = ArgparsePcDoctorParser( __doc__, bootstrap_log ),
        config_loader    = SssfConfigLoader(          bootstrap_log ),
        session_factory  = SessionRunFactory(         bootstrap_log ),
        phase_executor   = ManualContextPhaseExecutor( bootstrap_log ),
        status_factory   = lambda url: DashboardPcStatus( url, bootstrap_log ),
        agent            = WindowsClaudeCli( CLAUDE_CWD, CLAUDE_MODEL, CLAUDE_TOOLS, windows_temp, bootstrap_log ),
        commands         = FirstFoundCommandSource( PC_CHECK_CANDIDATES, bootstrap_log ),
        planners_factory = lambda request: [
            KnownCachePlanner( WIN_HOME, WSL_HOME, bootstrap_log ),
            StaleEnvironmentPlanner( ( WSL_HOME, WIN_HOME ), request.idle_days, 5, bootstrap_log ), ],
        policy           = RegenerableOnlyPolicy(
            allowed_roots = ( WIN_HOME, Path( "/mnt/c/Windows/Temp" ),
                              Path( "/mnt/c/ProgramData/Microsoft/Windows/WER" ), WSL_HOME ),
            protected     = ( WSL_HOME / ".claude", WSL_HOME / ".ssh", WSL_HOME / ".gnupg",
                              WSL_HOME / ".config", WIN_HOME / ".claude", WIN_HOME / ".ssh",
                              WSL_HOME / "agent_blocks/lancedb_memory/.venv" ),
            logger        = bootstrap_log ),
        remover          = SideRoutingRemover( WindowsRemover( windows_temp, bootstrap_log ),
                                               PosixRemover( bootstrap_log ), bootstrap_log ),
        alert_factory    = lambda folder: AllAlerts( WindowsToastAlert( windows_temp, bootstrap_log ),
                                                     FileAlert( folder, bootstrap_log )),
        estimator_factory = lambda sessions: PastRunsEstimator( sessions, DEFAULT_PHASE_SECONDS, bootstrap_log ),
        heads_up         = WindowsToastAlert( windows_temp, bootstrap_log ),
        surveyor         = RobocopySurveyor( windows_temp, SURVEY_FOLDER_SECONDS, SURVEY_PARALLEL, bootstrap_log ),
        question_factory = lambda url: DashboardQuestion( url, "adw_interface_pc_doctor", bootstrap_log ),
        compactor        = UvWorkflowRunner( COMPACT_ADW, bootstrap_log ),
        logger           = bootstrap_log, )


if __name__ == "__main__":
    sys.exit( build_application().run( sys.argv[ 1: ]))
