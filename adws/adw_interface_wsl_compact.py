#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Interface WSL Compact -- turn WSL off and compact its disk file, so the
space freed inside WSL comes back to C:.

WSL keeps its whole Linux filesystem in one ext4.vhdx on C:. That file only
ever grows: deleting files inside WSL frees blocks inside the file (the root
is mounted with `discard`, so they are handed back as unused), but the file
on C: keeps its size until it is compacted. On this box that is a 137 GB
file holding 125 GB of data -- ~12 GB that only a compaction gives back --
and it is the drastic measure pc_doctor stops short of, because:

* the disk file can only be compacted with WSL off (`wsl --shutdown`), and
  that stops EVERYTHING in WSL: Letta, Docker, MySQL, every Claude session
  open in it, and this ADW itself;
* Windows 10 Home has no Hyper-V `Optimize-VHD`, so it is `diskpart`'s
  `compact vdisk`, which needs an administrator (one UAC prompt);
* this box's "Letta WSL Tailscale Keepalive" task restarts WSL a minute
  after it stops, which would grab the disk file back mid-compaction -- and
  disabling it needs an administrator too.

So the work is split by who can survive what:

* this ADW (inside WSL) measures, trims, writes two PowerShell scripts and a
  plan into %TEMP%\\adw_wsl_compact\\<adw_id>\\, registers them as a
  one-off scheduled task, and finishes -- writing a `release` file as its
  last act, so the task knows the ADW (and pc_doctor, if that is who ran it)
  have recorded their result before WSL goes away.
* stage A (the scheduled task, as the user, outside WSL) waits for
  `release` plus a grace period, then asks for elevation. Declined, and
  nothing has been touched: WSL is still up.
* stage B (elevated) pauses the keepalive task, shuts WSL down, waits until
  the disk file can be opened exclusively, runs diskpart (attach read-only,
  compact, detach), and puts the keepalive back -- which is what starts WSL
  again, the way it does at logon.
* stage A then makes sure WSL is up, toasts the result, and runs this ADW
  again with `--report <adw_id>`, which joins the same session, records the
  before/after sizes, and asks the dashboard whether the alarm cleared.

The dashboard's own WSL (on DESKTOP-2OBSQMC) is held up by a service in this
box's WSL, so it may idle out while this one is down and come back with it;
the report's dashboard read retries for a few minutes because of that.

Usage (from /home/adamsl/agent_blocks):
    uv run adws/adw_interface_wsl_compact.py "<why>" [--dry-run] [--rehearse] \\
        [--min-gain-gb 2] [--grace-seconds 30] [--from-adw <pc_doctor adw_id>] \\
        [--message-out /tmp/line.txt] [--config adws/adw_sssf_config/sssf.config.yaml] [--adw-id a1b2c3d4]
    uv run adws/adw_interface_wsl_compact.py --report <adw_id>

`--dry-run` measures and stops: nothing is written to Windows, nothing runs.
`--rehearse` does everything except the UAC prompt, the shutdown and diskpart:
the scheduled task runs, waits for the release, and reports back -- a safe
test of the whole hand-off, with WSL staying up.

Phases: engineer(request) -> code(locate_disk) -> code(measure) -> code(preflight)
        -> code(trim) -> code(stage) -> code(hand_off)
        [--report: code(read_result) -> code(status_after) -> code(notify)]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from sssf_roles import ( ConsoleLifecycleLog, IApplication, IArgumentParser, IConfigLoader,
                         ILifecycleLog, IPhase, IPhaseExecutor, IPhaseHandle, ISessionFactory,
                         LifecycleLogged, ManualContextPhaseExecutor, PrintLifecycleLog,
                         SessionRunFactory, SssfConfigLoader )

from adw_modules import utils
from adw_modules.data_types import PhaseParams
from adw_modules.runner import Run

REQUIRED_AGENTS: list[ str ] = []   # no agents: every phase is code

_HERE = Path( __file__ ).resolve().parent


def _pc_doctor():
    """pc_doctor's module, loaded by file location: the toast, the dashboard
    reader and the path helpers are borrowed from it, not copied."""
    cached = sys.modules.get( "adw_interface_pc_doctor" )
    if cached is not None:
        return cached
    path = _HERE / "adw_interface_pc_doctor.py"
    if not path.is_file():
        raise ImportError( f"Could not find { path }. This workflow reuses its toast and dashboard reader." )
    if str( _HERE ) not in sys.path:
        sys.path.insert( 0, str( _HERE ))
    spec = importlib.util.spec_from_file_location( "adw_interface_pc_doctor", path )
    if spec is None or spec.loader is None:
        raise ImportError( f"Could not build an import spec for { path }" )
    module = importlib.util.module_from_spec( spec )
    sys.modules[ "adw_interface_pc_doctor" ] = module
    try:
        spec.loader.exec_module( module )
    except BaseException:
        del sys.modules[ "adw_interface_pc_doctor" ]
        raise
    return module


_doctor = _pc_doctor()

IUserAlert          = _doctor.IUserAlert
IPcStatusSource     = _doctor.IPcStatusSource
PcStatus            = _doctor.PcStatus
WindowsToastAlert   = _doctor.WindowsToastAlert
DashboardPcStatus   = _doctor.DashboardPcStatus
MONITOR_FOR_HOST    = _doctor.MONITOR_FOR_HOST
DEFAULT_DASHBOARD_URL = _doctor.DEFAULT_DASHBOARD_URL
WIN_HOME            = _doctor.WIN_HOME
_windows_path       = _doctor._windows_path
_wsl_path           = _doctor._wsl_path
_gb                 = _doctor._gb

WORK_ROOT = WIN_HOME / "AppData/Local/Temp/adw_wsl_compact"
#: The task that restarts WSL a minute after it stops; paused for the compaction.
KEEPALIVE_TASK = os.environ.get( "WSL_COMPACT_KEEPALIVE_TASK", "Letta WSL Tailscale Keepalive" )
UV = shutil.which( "uv" ) or str( Path.home() / ".local/bin/uv" )
REPO = _HERE.parent

DEFAULT_MIN_GAIN_GB = 2.0
DEFAULT_GRACE_SECONDS = 30
#: Stage A gives up -- touching nothing -- if the ADW never writes `release`.
RELEASE_WAIT_SECONDS = 15 * 60
#: Stage B gives up -- and puts the keepalive back -- if WSL won't let go of the file.
SHUTDOWN_WAIT_SECONDS = 180
#: The dashboard may be coming back up with this box's WSL; keep asking this long.
REPORT_STATUS_WAIT_SECONDS = 5 * 60


# -- Values ------------------------------------------------------------------

@dataclass
class WslCompactRequest:
    prompt: str
    dry_run: bool
    rehearse: bool
    min_gain_gb: float
    grace_seconds: int
    from_adw: str
    report: str
    pc: str
    dashboard_url: str
    message_out: str
    config: str
    adw_id: Optional[ str ]


class WslDisk( BaseModel ):
    distro: str
    vhdx_windows: str       # C:\...\ext4.vhdx, for the Windows-side scripts
    vhdx_wsl: str           # /mnt/c/.../ext4.vhdx, for measuring from here
    vhdx_bytes: int
    c_free_bytes: int


class DiskGain( BaseModel ):
    used_inside_bytes: int
    reclaimable_bytes: int  # vhdx size minus what WSL's filesystem holds: an upper bound

    def summary( self, disk: WslDisk ) -> str:
        return ( f"{ disk.distro }'s disk file is { _gb( disk.vhdx_bytes )} holding { _gb( self.used_inside_bytes )}; "
                 f"compacting can give back up to { _gb( self.reclaimable_bytes )} (C: has { _gb( disk.c_free_bytes )} free)" )


class HandOffPlan( BaseModel ):
    adw_id: str
    distro: str
    vhdx: str
    work_dir: str           # Windows path of the plan's folder
    task_name: str
    keepalive_task: str
    grace_seconds: int
    release_wait_seconds: int
    shutdown_wait_seconds: int
    rehearse: bool
    repo: str               # this repo, as WSL sees it, for the --report run
    uv: str


class CompactResult( BaseModel ):
    """result.json, as stage A writes it."""
    adw_id: str = ""
    outcome: str = "unknown"     # compacted | rehearsed | declined | failed | unknown
    detail: str = ""
    size_before: Optional[ int ] = None
    size_after: Optional[ int ] = None
    c_free_before: Optional[ int ] = None
    c_free_after: Optional[ int ] = None
    compact_seconds: Optional[ float ] = None
    keepalive: str = ""
    diskpart_output: str = ""
    started: str = ""
    ended: str = ""

    @property
    def freed_bytes( self ) -> int:
        if self.size_before is None or self.size_after is None:
            return 0
        return max( 0, self.size_before - self.size_after )

    def summary( self ) -> str:
        if self.outcome == "compacted":
            return ( f"WSL disk compacted: { _gb( self.size_before or 0 )} -> { _gb( self.size_after or 0 )} "
                     f"({ _gb( self.freed_bytes )} back), C: free { _gb( self.c_free_before or 0 )} -> "
                     f"{ _gb( self.c_free_after or 0 )}, diskpart took { round(( self.compact_seconds or 0 ) / 60, 1 )} min" )
        if self.outcome == "rehearsed":
            return "Rehearsal finished: the hand-off, release and report all ran; WSL was never stopped."
        if self.outcome == "declined":
            return f"Not compacted: the UAC prompt was declined, so WSL was never stopped. { self.detail }".strip()
        return f"WSL compaction { self.outcome }: { self.detail }".strip()


# -- Roles -------------------------------------------------------------------

class IWindowsShell( ABC ):
    """PowerShell on the Windows side of this box."""

    @abstractmethod
    def run( self, script: str, timeout_seconds: int ) -> str: ...


class IDiskLocator( ABC ):
    @abstractmethod
    def locate( self ) -> WslDisk: ...


class IHandOff( ABC ):
    """Puts the Windows-side worker in place and starts it."""

    @abstractmethod
    def stage( self, plan: HandOffPlan, stage_a: str, stage_b: str ) -> Path: ...

    @abstractmethod
    def start( self, plan: HandOffPlan ) -> str: ...

    @abstractmethod
    def release( self, plan: HandOffPlan ) -> None: ...


# -- Adapters ----------------------------------------------------------------

class PowerShellExe( LifecycleLogged, IWindowsShell ):
    """powershell.exe through WSL interop. Raises on a non-zero exit."""

    def __init__( self, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )

    def run( self, script: str, timeout_seconds: int ) -> str:
        problem = ""
        # Twice: WSL interop now and then drops one launch (UtilAcceptVsock).
        for _ in range( 2 ):
            try:
                done = subprocess.run( [ "powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script ],
                                       capture_output=True, text=True, timeout=timeout_seconds, cwd="/mnt/c" )
            except OSError as error:
                problem = str( error )
                continue
            if done.returncode == 0:
                return done.stdout.replace( "\r", "" ).strip()
            problem = ( done.stderr or done.stdout ).replace( "\r", "" ).strip()[ :400 ]
        raise RuntimeError( f"powershell failed: { problem }" )


class RegistryDiskLocator( LifecycleLogged, IDiskLocator ):
    """Finds this distro's ext4.vhdx from WSL's own registration (HKCU\\...\\Lxss)."""

    def __init__( self, shell: IWindowsShell, distro: str, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._shell  = shell
        self._distro = distro

    def locate( self ) -> WslDisk:
        script = ( "$root = 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Lxss'; "
                   "$default = (Get-ItemProperty $root).DefaultDistribution; "
                   "Get-ChildItem $root | ForEach-Object { $p = Get-ItemProperty $_.PSPath; "
                   "[pscustomobject]@{ name = $p.DistributionName; base = $p.BasePath; version = $p.Version; "
                   "default = ($_.PSChildName -eq $default) } } | ConvertTo-Json -Compress; "
                   "'::free::' + (Get-PSDrive C).Free" )
        out = self._shell.run( script, 60 )
        listing, _, free = out.partition( "::free::" )
        rows = json.loads( listing.strip() or "[]" )
        rows = rows if isinstance( rows, list ) else [ rows ]
        wanted = [ r for r in rows if r[ "name" ] == self._distro ] if self._distro else [ r for r in rows if r[ "default" ]]
        if not wanted:
            raise RuntimeError( f"no WSL distro { self._distro or '(default)' } in the registry; found { [ r[ 'name' ] for r in rows ]}" )
        row = wanted[ 0 ]
        if row.get( "version" ) != 2:
            raise RuntimeError( f"{ row[ 'name' ]} is a WSL 1 distro: it has no disk file to compact" )
        base = str( row[ "base" ]).removeprefix( "\\\\?\\" )
        vhdx_windows = base.rstrip( "\\" ) + "\\ext4.vhdx"
        vhdx_wsl = _wsl_path( vhdx_windows )
        if not Path( vhdx_wsl ).is_file():
            raise RuntimeError( f"{ row[ 'name' ]}'s disk file is not where WSL says: { vhdx_windows }" )
        return WslDisk( distro=row[ "name" ], vhdx_windows=vhdx_windows, vhdx_wsl=vhdx_wsl,
                        vhdx_bytes=Path( vhdx_wsl ).stat().st_size, c_free_bytes=int( free.strip() or 0 ))


class ScheduledTaskHandOff( LifecycleLogged, IHandOff ):
    """A one-off scheduled task: owned by Task Scheduler, not by WSL, so it
    outlives `wsl --shutdown`; run as the logged-on user, so the UAC prompt
    and the toast reach the desktop."""

    def __init__( self, shell: IWindowsShell, work_root: Path, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._shell = shell
        self._root  = work_root

    def _folder( self, plan: HandOffPlan ) -> Path:
        return self._root / plan.adw_id

    def stage( self, plan: HandOffPlan, stage_a: str, stage_b: str ) -> Path:
        folder = self._folder( plan )
        folder.mkdir( parents=True, exist_ok=True )
        for stale in ( "release", "keepalive_stopped", "compact.json", "result.json" ):
            ( folder / stale ).unlink( missing_ok=True )
        ( folder / "plan.json" ).write_text( plan.model_dump_json( indent=2 ), encoding="utf-8" )
        # Windows PowerShell 5 reads a BOM-less script as ANSI; the BOM keeps it UTF-8.
        ( folder / "stage_a.ps1" ).write_text( stage_a, encoding="utf-8-sig" )
        ( folder / "stage_b.ps1" ).write_text( stage_b, encoding="utf-8-sig" )
        return folder

    def start( self, plan: HandOffPlan ) -> str:
        script_path = plan.work_dir + "\\stage_a.ps1"
        quote = lambda s: "'" + s.replace( "'", "''" ) + "'"
        args = f'-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{ script_path }" "{ plan.work_dir }"'
        script = ( f"$a = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument { quote( args )}; "
                   "$p = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited; "
                   "$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
                   "-ExecutionTimeLimit (New-TimeSpan -Hours 6); "
                   f"Register-ScheduledTask -TaskName { quote( plan.task_name )} -Action $a -Principal $p -Settings $s -Force | Out-Null; "
                   f"Start-ScheduledTask -TaskName { quote( plan.task_name )}; "
                   f"(Get-ScheduledTask -TaskName { quote( plan.task_name )}).State" )
        return self._shell.run( script, 60 )

    def release( self, plan: HandOffPlan ) -> None:
        ( self._folder( plan ) / "release" ).write_text( time.strftime( "%Y-%m-%dT%H:%M:%S" ), encoding="utf-8" )


# Stage A: the user's scheduled task. Survives WSL; never shuts it down itself
# until stage B (elevated) has paused the keepalive; always brings WSL back.
_STAGE_A_PS1 = r"""
param( [string] $Dir )
$ErrorActionPreference = 'Stop'
$plan = Get-Content -Raw ( Join-Path $Dir 'plan.json' ) | ConvertFrom-Json
function Log( $m ) { Add-Content -Path ( Join-Path $Dir 'worker.log' ) -Value "$( Get-Date -Format s ) A $m" }
function Toast( $title, $body ) {
  try {
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
    $t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent( [Windows.UI.Notifications.ToastTemplateType]::ToastText02 )
    $x = $t.GetElementsByTagName( 'text' )
    $x.Item( 0 ).AppendChild( $t.CreateTextNode( $title )) > $null
    $x.Item( 1 ).AppendChild( $t.CreateTextNode( $body )) > $null
    $app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier( $app ).Show( [Windows.UI.Notifications.ToastNotification]::new( $t ))
  } catch { Log "toast failed: $_" }
}
$wsl = Join-Path $env:WINDIR 'System32\wsl.exe'
$result = [ordered]@{ adw_id = $plan.adw_id; outcome = 'failed'; detail = ''; size_before = $null; size_after = $null;
                      c_free_before = $null; c_free_after = $null; compact_seconds = $null; keepalive = '';
                      diskpart_output = ''; started = ( Get-Date -Format o ); ended = '' }
$touched = $false
try {
  Log 'waiting for the ADW to release the hand-off'
  $deadline = ( Get-Date ).AddSeconds( $plan.release_wait_seconds )
  while ( -not ( Test-Path ( Join-Path $Dir 'release' ))) {
    if (( Get-Date ) -gt $deadline ) { throw 'the ADW never released the hand-off; nothing was touched' }
    Start-Sleep -Seconds 2
  }
  Log "released; grace $( $plan.grace_seconds )s"
  Start-Sleep -Seconds $plan.grace_seconds
  $result.size_before = ( Get-Item -LiteralPath $plan.vhdx ).Length
  $result.c_free_before = ( Get-PSDrive C ).Free
  if ( $plan.rehearse ) {
    $result.outcome = 'rehearsed'
    $result.detail = 'rehearsal: no UAC prompt, no shutdown, no diskpart'
    return
  }
  Log 'asking for elevation (UAC)'
  $bArgs = @( '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', "`"$( Join-Path $Dir 'stage_b.ps1' )`"", "`"$Dir`"" )
  try {
    $b = Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $bArgs -PassThru
  } catch {
    $result.outcome = 'declined'
    $result.detail = "$_"
    return
  }
  $touched = $true
  # Belt and braces: once B has paused the keepalive, shut WSL down from the
  # user's side too, in case an elevated `wsl --shutdown` reaches another instance.
  while ( -not $b.HasExited ) {
    if ( Test-Path ( Join-Path $Dir 'keepalive_stopped' )) {
      & $wsl --shutdown 2>&1 | Out-Null
      break
    }
    Start-Sleep -Seconds 1
  }
  $b.WaitForExit()
  Log 'stage B finished'
  $compactFile = Join-Path $Dir 'compact.json'
  if ( -not ( Test-Path $compactFile )) { throw 'stage B left no compact.json' }
  $c = Get-Content -Raw $compactFile | ConvertFrom-Json
  $result.outcome = $c.outcome
  $result.detail = $c.detail
  $result.compact_seconds = $c.compact_seconds
  $result.keepalive = $c.keepalive
  $result.diskpart_output = $c.diskpart_output
} catch {
  $result.detail = "$_"
  Log "error: $_"
} finally {
  if ( $touched ) {
    Log 'starting WSL again'
    for ( $i = 0; $i -lt 3; $i++ ) {
      & $wsl -d $plan.distro --exec /bin/true 2>&1 | Out-Null
      if ( $LASTEXITCODE -eq 0 ) { break }
      Start-Sleep -Seconds 10
    }
  }
  $result.size_after = ( Get-Item -LiteralPath $plan.vhdx ).Length
  $result.c_free_after = ( Get-PSDrive C ).Free
  $result.ended = ( Get-Date -Format o )
  $result | ConvertTo-Json | Set-Content -Path ( Join-Path $Dir 'result.json' ) -Encoding UTF8
  $freed = 0
  if ( $result.size_before -and $result.size_after ) { $freed = [math]::Round(( $result.size_before - $result.size_after ) / 1GB, 2 ) }
  Toast "WSL compaction: $( $result.outcome )" "Disk file gave back $freed GB. C: free $( [math]::Round( $result.c_free_after / 1GB, 2 )) GB. $( $result.detail )"
  Log 'recording the result in the ADW session'
  $report = "cd '$( $plan.repo )' && '$( $plan.uv )' run adws/adw_interface_wsl_compact.py --report $( $plan.adw_id )"
  & $wsl -d $plan.distro --exec /bin/bash -lc $report 2>&1 | Out-File -FilePath ( Join-Path $Dir 'report.log' ) -Encoding utf8 -Append
  Log "report exited $LASTEXITCODE"
  Unregister-ScheduledTask -TaskName $plan.task_name -Confirm:$false -ErrorAction SilentlyContinue
}
"""

# Stage B: elevated. Pause the keepalive, stop WSL, compact, put the keepalive back.
_STAGE_B_PS1 = r"""
param( [string] $Dir )
$ErrorActionPreference = 'Stop'
$plan = Get-Content -Raw ( Join-Path $Dir 'plan.json' ) | ConvertFrom-Json
function Log( $m ) { Add-Content -Path ( Join-Path $Dir 'worker.log' ) -Value "$( Get-Date -Format s ) B $m" }
$wsl = Join-Path $env:WINDIR 'System32\wsl.exe'
$out = [ordered]@{ outcome = 'failed'; detail = ''; compact_seconds = $null; diskpart_output = ''; keepalive = '' }
$ka = $plan.keepalive_task
$kaWasEnabled = $false
$attached = $false
try {
  if ( $ka -and ( Get-ScheduledTask -TaskName $ka -ErrorAction SilentlyContinue )) {
    $kaWasEnabled = ( Get-ScheduledTask -TaskName $ka ).State -ne 'Disabled'
    Disable-ScheduledTask -TaskName $ka | Out-Null
    Stop-ScheduledTask -TaskName $ka
    Log "paused keepalive task '$ka'"
  }
  Set-Content -Path ( Join-Path $Dir 'keepalive_stopped' ) -Value ( Get-Date -Format s )
  & $wsl --shutdown 2>&1 | Out-Null
  Log 'wsl --shutdown sent; waiting for the disk file to be released'
  $deadline = ( Get-Date ).AddSeconds( $plan.shutdown_wait_seconds )
  while ( $true ) {
    try {
      $fs = [IO.File]::Open( $plan.vhdx, 'Open', 'ReadWrite', 'None' )
      $fs.Close()
      break
    } catch {
      if (( Get-Date ) -gt $deadline ) { throw "WSL did not let go of the disk file within $( $plan.shutdown_wait_seconds )s; not compacted" }
      Start-Sleep -Seconds 3
      & $wsl --shutdown 2>&1 | Out-Null
    }
  }
  $script = Join-Path $Dir 'diskpart.txt'
  Set-Content -Path $script -Encoding ASCII -Value @( "select vdisk file=`"$( $plan.vhdx )`"", 'attach vdisk readonly', 'compact vdisk', 'detach vdisk', 'exit' )
  Log 'diskpart compact starting'
  $attached = $true
  $sw = [Diagnostics.Stopwatch]::StartNew()
  $text = & diskpart.exe /s $script 2>&1 | Out-String
  $code = $LASTEXITCODE
  $out.compact_seconds = [math]::Round( $sw.Elapsed.TotalSeconds, 1 )
  $out.diskpart_output = $text.Trim()
  Log "diskpart exited $code after $( $out.compact_seconds )s"
  if ( $code -ne 0 -or $text -notmatch 'successfully compacted' ) { throw "diskpart exit $code" }
  if ( $text -match 'successfully detached' ) { $attached = $false }
  $out.outcome = 'compacted'
} catch {
  $out.detail = "$_"
  Log "error: $_"
} finally {
  if ( $attached ) {
    # Never leave the disk attached to Windows: WSL could not start on it.
    $detach = Join-Path $Dir 'diskpart_detach.txt'
    Set-Content -Path $detach -Encoding ASCII -Value @( "select vdisk file=`"$( $plan.vhdx )`"", 'detach vdisk', 'exit' )
    & diskpart.exe /s $detach 2>&1 | Out-Null
  }
  if ( $ka -and $kaWasEnabled ) {
    try {
      Enable-ScheduledTask -TaskName $ka | Out-Null
      Start-ScheduledTask -TaskName $ka
      $out.keepalive = "re-enabled and started '$ka'"
    } catch { $out.keepalive = "could not restore '$ka': $_" }
    Log $out.keepalive
  }
  $out | ConvertTo-Json | Set-Content -Path ( Join-Path $Dir 'compact.json' ) -Encoding UTF8
}
"""


# -- Phases ------------------------------------------------------------------

class RequestCapturePhase( LifecycleLogged, IPhase ):
    """Records why and how this compaction was asked for."""

    def __init__( self, engineer: str, request: WslCompactRequest, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._engineer = engineer
        self._request  = request

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="request", kind="engineer", owner=self._engineer,
                            description="Record why WSL is being compacted and under what options" )

    def act( self, handle: IPhaseHandle ) -> None:
        r = self._request
        handle.log( input=r.prompt, dry_run=r.dry_run, rehearse=r.rehearse, min_gain_gb=r.min_gain_gb,
                    from_adw=r.from_adw or "(run by hand)" )


class LocateDiskPhase( LifecycleLogged, IPhase ):
    def __init__( self, locator: IDiskLocator, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._locator = locator

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="locate_disk", kind="code", owner="wsl_compact",
                            description="Find this distro's ext4.vhdx from WSL's own registration" )

    def act( self, handle: IPhaseHandle ) -> WslDisk:
        disk = self._locator.locate()
        handle.log( distro=disk.distro, vhdx=disk.vhdx_windows, vhdx_size=_gb( disk.vhdx_bytes ),
                    c_free=_gb( disk.c_free_bytes ))
        return disk


class MeasurePhase( LifecycleLogged, IPhase ):
    """How much a compaction could give back: the file's size less what the
    filesystem inside it holds. An upper bound -- ext4's own metadata stays."""

    def __init__( self, disk: WslDisk, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._disk = disk

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="measure", kind="code", owner="wsl_compact",
                            description="Compare the disk file's size with what WSL's filesystem actually holds" )

    def act( self, handle: IPhaseHandle ) -> DiskGain:
        st = os.statvfs( "/" )
        used = ( st.f_blocks - st.f_bfree ) * st.f_frsize
        gain = DiskGain( used_inside_bytes=used, reclaimable_bytes=max( 0, self._disk.vhdx_bytes - used ))
        handle.log( used_inside=_gb( used ), reclaimable=_gb( gain.reclaimable_bytes ),
                    summary=gain.summary( self._disk ))
        return gain


class PreflightPhase( LifecycleLogged, IPhase ):
    """Names what `wsl --shutdown` will stop, so the record says what went down."""

    def __init__( self, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="preflight", kind="code", owner="wsl_compact",
                            description="List what shutting WSL down will stop" )

    @staticmethod
    def _lines( argv: list[ str ]) -> list[ str ]:
        try:
            done = subprocess.run( argv, capture_output=True, text=True, timeout=30 )
        except ( OSError, subprocess.TimeoutExpired ):
            return []
        return [ line.strip() for line in done.stdout.splitlines() if line.strip()]

    def act( self, handle: IPhaseHandle ) -> list[ str ]:
        containers = self._lines([ "docker", "ps", "--format", "{{.Names}}" ])
        services = [ line.split()[ 0 ] for line in self._lines(
            [ "systemctl", "list-units", "--type=service", "--state=running", "--no-legend", "--plain" ])]
        services = [ s for s in services if not s.startswith(( "systemd-", "dbus", "getty", "user@", "polkit",
                                                                "rtkit", "udisks", "wsl-pro", "cron", "rsyslog" ))]
        claude = [ line for line in self._lines([ "pgrep", "-a", "-f", "claude" ]) if " claude" in f" { line }" ]
        stopping = ( [ f"docker: { ', '.join( containers )}" ] if containers else [] ) + \
                   ( [ f"services: { ', '.join( services )}" ] if services else [] ) + \
                   ( [ f"{ len( claude )} Claude process(es) running in WSL" ] if claude else [] )
        handle.log( will_stop=stopping or [ "nothing notable" ])
        return stopping


class TrimPhase( LifecycleLogged, IPhase ):
    """fstrim, as root, through wsl.exe -- no sudo password. The root is
    already mounted with `discard`, so this mostly catches what that missed."""

    def __init__( self, distro: str, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._distro = distro

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="trim", kind="code", owner="wsl_compact",
                            description="Hand every free block back to the disk file before it is compacted" )

    def act( self, handle: IPhaseHandle ) -> str:
        try:
            done = subprocess.run( [ "wsl.exe", "-d", self._distro, "-u", "root", "--exec", "fstrim", "-av" ],
                                   capture_output=True, text=True, timeout=15 * 60, cwd="/mnt/c" )
            text = ( done.stdout or done.stderr ).replace( "\x00", "" ).strip()
        except ( OSError, subprocess.TimeoutExpired ) as error:
            text = f"fstrim did not run: { error }"
        handle.log( fstrim=text[ :400 ] or "(no output)" )
        return text


class StagePhase( LifecycleLogged, IPhase ):
    def __init__( self, hand_off: IHandOff, plan: HandOffPlan, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._hand_off = hand_off
        self._plan     = plan

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="stage", kind="code", owner="wsl_compact",
                            description="Write the plan and the two Windows-side scripts that outlive WSL" )

    def act( self, handle: IPhaseHandle ) -> Path:
        folder = self._hand_off.stage( self._plan, _STAGE_A_PS1, _STAGE_B_PS1 )
        handle.log( folder=str( folder ), task=self._plan.task_name, rehearse=self._plan.rehearse )
        return folder


class HandOffPhase( LifecycleLogged, IPhase ):
    """Starts the scheduled task and releases it. The last thing this ADW
    does: after `release` and the grace period, WSL is no longer safe to be in."""

    def __init__( self, hand_off: IHandOff, plan: HandOffPlan, heads_up: IUserAlert, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._hand_off = hand_off
        self._plan     = plan
        self._heads_up = heads_up

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="hand_off", kind="code", owner="wsl_compact",
                            description="Start the Windows-side worker and release it to shut WSL down" )

    def act( self, handle: IPhaseHandle ) -> str:
        state = self._hand_off.start( self._plan )
        if self._plan.rehearse:
            toast = self._heads_up.alert( "WSL compaction rehearsal",
                                          f"The hand-off runs in { self._plan.grace_seconds }s; WSL stays up." )
        else:
            toast = self._heads_up.alert(
                "WSL compaction: approve the UAC prompt",
                f"In { self._plan.grace_seconds }s Windows asks for admin rights. Yes shuts WSL down "
                "(Letta, Docker, Claude sessions in WSL) while its disk is compacted; No leaves it all running." )
        self._hand_off.release( self._plan )
        handle.log( task_state=state, toast=toast, released=True )
        return state


class ReadResultPhase( LifecycleLogged, IPhase ):
    def __init__( self, result_path: Path, session_dir: Path, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._result_path = result_path
        self._session_dir = session_dir

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="read_result", kind="code", owner="wsl_compact",
                            description="Read what the Windows-side worker did and keep its log with the session" )

    def act( self, handle: IPhaseHandle ) -> CompactResult:
        if not self._result_path.is_file():
            result = CompactResult( outcome="unknown", detail=f"no result at { self._result_path }" )
        else:
            result = CompactResult.model_validate_json( self._result_path.read_text( encoding="utf-8-sig" ))
        for name in ( "result.json", "worker.log", "diskpart.txt" ):
            src = self._result_path.parent / name
            if src.is_file():
                shutil.copyfile( src, self._session_dir / f"compact_{ name }" )
        handle.log( outcome=result.outcome, freed=_gb( result.freed_bytes ), keepalive=result.keepalive or "-",
                    summary=result.summary())
        return result


class StatusAfterPhase( LifecycleLogged, IPhase ):
    """The dashboard's verdict, retried while it comes back up with WSL."""

    def __init__( self, source: IPcStatusSource, pc: str, wait_seconds: int, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._source = source
        self._pc     = pc
        self._wait   = wait_seconds

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="status_after", kind="code", owner="dashboard",
                            description="Ask the dashboard whether the compaction cleared the alarm" )

    def act( self, handle: IPhaseHandle ) -> Optional[ PcStatus ]:
        deadline = time.monotonic() + self._wait
        problem = ""
        while True:
            try:
                status = self._source.read( self._pc )
                handle.log( level=status.level, status=status.describe())
                return status
            except RuntimeError as error:
                problem = str( error )
            if time.monotonic() > deadline:
                handle.log( level="unknown", status=f"dashboard not answering: { problem }" )
                return None
            time.sleep( 20 )


class NotifyPhase( LifecycleLogged, IPhase ):
    def __init__( self, alert: IUserAlert, result: CompactResult, status: Optional[ PcStatus ],
                  report_path: Path, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._alert  = alert
        self._result = result
        self._status = status
        self._report = report_path

    @property
    def params( self ) -> PhaseParams:
        return PhaseParams( name="notify", kind="code", owner="wsl_compact",
                            description="Write COMPACT_RESULT.md and tell the user how it went" )

    def act( self, handle: IPhaseHandle ) -> str:
        r = self._result
        if self._status:
            verdict = self._status.describe()
        elif r.outcome == "rehearsed":
            verdict = "A rehearsal does not ask the dashboard: nothing on the disk changed."
        else:
            verdict = "The dashboard did not answer."
        lines = [ f"# WSL compaction: { r.outcome }", "", r.summary(), "", verdict ]
        if r.keepalive:
            lines += [ "", f"Keepalive: { r.keepalive }" ]
        if r.diskpart_output:
            lines += [ "", "```", r.diskpart_output, "```" ]
        self._report.write_text( "\n".join( lines ) + "\n", encoding="utf-8" )
        still = "" if not self._status or self._status.healthy else f" Still { self._status.level.upper()}."
        where = self._alert.alert( f"WSL compaction: { r.outcome }", r.summary() + still )
        handle.log( report=str( self._report ), alerted=where )
        return r.summary() + still


# -- Argument parsing --------------------------------------------------------

class ArgparseWslCompactParser( LifecycleLogged, IArgumentParser ):
    """The only class allowed to know this CLI is built on argparse."""

    def __init__( self, description: str, logger: ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._description = description

    def parse( self, argv: list[ str ]) -> WslCompactRequest:
        parser = argparse.ArgumentParser( description=self._description,
                                          formatter_class=argparse.RawDescriptionHelpFormatter )
        parser.add_argument( "prompt", nargs="?", default="Compact WSL's disk to give space back to C:",
                             help="inline text or a path to a prompt file; why this is being run" )
        parser.add_argument( "--dry-run", action="store_true", help="measure and stop; write and run nothing" )
        parser.add_argument( "--rehearse", action="store_true",
                             help="run the whole hand-off but skip UAC, the shutdown and diskpart" )
        parser.add_argument( "--min-gain-gb", type=float, default=DEFAULT_MIN_GAIN_GB,
                             help="refuse when compacting could give back less than this" )
        parser.add_argument( "--grace-seconds", type=int, default=DEFAULT_GRACE_SECONDS,
                             help="how long after the release before WSL is touched" )
        parser.add_argument( "--from-adw", default="", help="the adw_id of the ADW that asked for this" )
        parser.add_argument( "--report", default="", metavar="ADW_ID",
                             help="(run by the Windows-side worker) record a hand-off's result" )
        parser.add_argument( "--dashboard-url", default=DEFAULT_DASHBOARD_URL )
        parser.add_argument( "--message-out", default="",
                             help="write the operator's one line here, for a non-terminal caller" )
        parser.add_argument( "--config", default="adws/adw_sssf_config/sssf.config.yaml" )
        parser.add_argument( "--adw-id", default=None, help="join or pin an existing session" )
        parsed = parser.parse_args( argv )
        return WslCompactRequest(
            prompt=utils.resolve_prompt( parsed.prompt ), dry_run=parsed.dry_run, rehearse=parsed.rehearse,
            min_gain_gb=parsed.min_gain_gb, grace_seconds=max( 10, parsed.grace_seconds ),
            from_adw=parsed.from_adw, report=parsed.report,
            pc=MONITOR_FOR_HOST.get( socket.gethostname().upper(), "" ), dashboard_url=parsed.dashboard_url,
            message_out=parsed.message_out, config=parsed.config,
            adw_id=parsed.report or parsed.adw_id )


# -- Composition root's one orchestrator -------------------------------------

class WslCompactApplication( LifecycleLogged, IApplication ):
    """Measures, stages and hands off; or, with --report, records how the
    hand-off went. Depends on roles, plus the concrete `Run` every phase shares."""

    def __init__( self,
                  argument_parser: IArgumentParser,
                  config_loader:   IConfigLoader,
                  session_factory: ISessionFactory,
                  phase_executor:  IPhaseExecutor,
                  locator:         IDiskLocator,
                  hand_off:        IHandOff,
                  status_factory:  object,
                  alert:           IUserAlert,
                  work_root:       Path,
                  logger:          ILifecycleLog ):
        LifecycleLogged.__init__( self, logger )
        self._argument_parser = argument_parser
        self._config_loader   = config_loader
        self._session_factory = session_factory
        self._phase_executor  = phase_executor
        self._locator         = locator
        self._hand_off        = hand_off
        self._status_factory  = status_factory
        self._alert           = alert
        self._work_root       = work_root

    def run( self, argv: list[ str ]) -> int:
        request = self._argument_parser.parse( argv )
        cfg     = self._config_loader.load( request.config )
        self._config_loader.validate( cfg, REQUIRED_AGENTS )
        run = self._session_factory.create( cfg, request.adw_id )
        log: ILifecycleLog = ConsoleLifecycleLog( run )
        execute = lambda phase: self._phase_executor.execute( run, phase )
        if request.report:
            return self._report( run, request, execute, log )

        execute( RequestCapturePhase( run.engineer, request, log ))
        disk = execute( LocateDiskPhase( self._locator, log ))
        gain = execute( MeasurePhase( disk, log ))
        if gain.reclaimable_bytes < request.min_gain_gb * 1024 ** 3:
            return self._finish( run, request, False,
                                 f"Not worth it: { gain.summary( disk )} -- under --min-gain-gb { request.min_gain_gb }." )
        if request.dry_run:
            return self._finish( run, request, True, f"Dry run. { gain.summary( disk )}." )

        execute( PreflightPhase( log ))
        if not request.rehearse:
            execute( TrimPhase( disk.distro, log ))
        plan = HandOffPlan(
            adw_id=run.adw_id, distro=disk.distro, vhdx=disk.vhdx_windows,
            work_dir=_windows_path( str( self._work_root / run.adw_id )),
            task_name=f"adw_wsl_compact_{ run.adw_id }", keepalive_task=KEEPALIVE_TASK,
            grace_seconds=request.grace_seconds, release_wait_seconds=RELEASE_WAIT_SECONDS,
            shutdown_wait_seconds=SHUTDOWN_WAIT_SECONDS, rehearse=request.rehearse,
            repo=str( REPO ), uv=UV )
        execute( StagePhase( self._hand_off, plan, log ))
        execute( HandOffPhase( self._hand_off, plan, self._alert, log ))
        what = "Rehearsal handed off" if request.rehearse else "Handed off"
        return self._finish( run, request, True,
                             f"{ what } to scheduled task { plan.task_name }: { gain.summary( disk )}. "
                             f"WSL is touched { request.grace_seconds }s from now, after the UAC prompt; "
                             f"the result lands in { run.session_dir } via --report." )

    def _report( self, run: Run, request: WslCompactRequest, execute, log: ILifecycleLog ) -> int:
        result = execute( ReadResultPhase( self._work_root / request.report / "result.json", run.session_dir, log ))
        status = None
        if request.pc and result.outcome != "rehearsed":
            status = execute( StatusAfterPhase( self._status_factory( request.dashboard_url ), request.pc,
                                                REPORT_STATUS_WAIT_SECONDS, log ))
        line = execute( NotifyPhase( self._alert, result, status, run.session_dir / "COMPACT_RESULT.md", log ))
        return self._finish( run, request, result.outcome in ( "compacted", "rehearsed" ), line )

    @staticmethod
    def _finish( run: Run, request: WslCompactRequest, accepted: bool, message: str ) -> int:
        print( message )
        if request.message_out:
            Path( request.message_out ).write_text( message + "\n" )
        return run.finish( accepted=accepted, reason=message.splitlines()[ 0 ])


def build_application() -> IApplication:
    """Wire the concrete adapters into the application. The one spot that may."""
    bootstrap_log: ILifecycleLog = PrintLifecycleLog()
    shell = PowerShellExe( bootstrap_log )
    windows_temp = WIN_HOME / "AppData/Local/Temp"
    return WslCompactApplication(
        argument_parser = ArgparseWslCompactParser( __doc__, bootstrap_log ),
        config_loader   = SssfConfigLoader(          bootstrap_log ),
        session_factory = SessionRunFactory(         bootstrap_log ),
        phase_executor  = ManualContextPhaseExecutor( bootstrap_log ),
        locator         = RegistryDiskLocator( shell, os.environ.get( "WSL_DISTRO_NAME", "" ), bootstrap_log ),
        hand_off        = ScheduledTaskHandOff( shell, WORK_ROOT, bootstrap_log ),
        status_factory  = lambda url: DashboardPcStatus( url, bootstrap_log ),
        alert           = WindowsToastAlert( windows_temp, bootstrap_log ),
        work_root       = WORK_ROOT,
        logger          = bootstrap_log, )


if __name__ == "__main__":
    sys.exit( build_application().run( sys.argv[ 1: ]))
