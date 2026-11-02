"""The Windows print queue, driven from WSL: IPrinterDiscovery and IPrintBackend.

Every PowerShell call runs a fixed script (a constant below, sent as
-EncodedCommand) and receives its parameters as JSON on stdin, so nothing a
request says is ever part of a command line. Discovery reads Get-Printer and
the default printer and changes nothing. Submission prints already-rendered
page images through System.Drawing's PrintDocument with the
StandardPrintController (no progress dialog), as one spooler job whose
DocumentName is `STB <job id>` -- which is how status and cancel find it again.
Virtual queues (PDF, XPS, OneNote, fax) are hidden: printing to them opens a
save dialog nobody is there to answer.
"""
from __future__ import annotations

import base64
import json
import re
import shutil
from pathlib import Path, PureWindowsPath

from ..errors import SpoolerError, SpoolerRefused
from ..interfaces import ICommandRunner
from ..models import JobState, PrinterInfo, PrintJob, PrintResult, PrintStatus

POWERSHELL = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
HIDDEN     = re.compile( r"pdf|xps|onenote|fax|send to|document writer", re.I )
NEEDS_HAND = ( "Error", "PaperOut", "Offline", "UserIntervention", "Blocked", "Paused" )

DISCOVER = r"""
$ErrorActionPreference = 'Stop'
$default = (Get-CimInstance Win32_Printer -Filter 'Default=TRUE' | Select-Object -First 1).Name
$list = @(Get-Printer | ForEach-Object {
  [pscustomobject]@{ name = $_.Name; driver = $_.DriverName; port = $_.PortName; shared = [bool]$_.Shared;
                     status = [string]$_.PrinterStatus; is_default = ($_.Name -eq $default) } })
[pscustomobject]@{ printers = $list } | ConvertTo-Json -Depth 4 -Compress
"""

SUBMIT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$spec = [Console]::In.ReadToEnd() | ConvertFrom-Json
$doc = New-Object System.Drawing.Printing.PrintDocument
$doc.PrinterSettings.PrinterName = $spec.printer
if (-not $doc.PrinterSettings.IsValid) { Write-Output '{"error":"printer_not_found"}'; exit 3 }
$doc.DocumentName = $spec.document_name
$pages = @($spec.pages)
$copies = [int]$spec.copies
if ($doc.PrinterSettings.MaximumCopies -ge $copies) {
  $doc.PrinterSettings.Copies = [int16]$copies; $doc.PrinterSettings.Collate = $true
} else {
  $one = $pages; $pages = @(); for ($c = 0; $c -lt $copies; $c++) { $pages += $one }
}
$doc.PrinterSettings.Duplex = [System.Drawing.Printing.Duplex]::Simplex
$doc.DefaultPageSettings.Color = $false
$doc.DefaultPageSettings.Landscape = [bool]$spec.landscape
$letter = $doc.PrinterSettings.PaperSizes | Where-Object { $_.Kind -eq [System.Drawing.Printing.PaperKind]::Letter } | Select-Object -First 1
if ($letter) { $doc.DefaultPageSettings.PaperSize = $letter }
$doc.PrintController = New-Object System.Drawing.Printing.StandardPrintController
$script:i = 0
$doc.add_PrintPage({ param($sender, $e)
  $img = [System.Drawing.Image]::FromFile($pages[$script:i])
  try {
    $w = $e.PageBounds.Width; $h = $e.PageBounds.Height
    $scale = [Math]::Min($w / $img.Width, $h / $img.Height)
    $dw = $img.Width * $scale; $dh = $img.Height * $scale
    $x = ($w - $dw) / 2 - $e.PageSettings.HardMarginX; $y = ($h - $dh) / 2 - $e.PageSettings.HardMarginY
    $e.Graphics.DrawImage($img, [single]$x, [single]$y, [single]$dw, [single]$dh)
  } finally { $img.Dispose() }
  $script:i++
  $e.HasMorePages = ($script:i -lt $pages.Count)
})
$doc.Print()
$job = Get-PrintJob -PrinterName $spec.printer -ErrorAction SilentlyContinue | Where-Object { $_.DocumentName -eq $spec.document_name } | Select-Object -First 1
if ($job) { [pscustomobject]@{ spooler_id = [int]$job.Id; flags = [string]$job.JobStatus } | ConvertTo-Json -Compress }
else { '{"spooler_id":null,"flags":""}' }
"""

STATUS = r"""
$ErrorActionPreference = 'Stop'
$spec = [Console]::In.ReadToEnd() | ConvertFrom-Json
$job = Get-PrintJob -PrinterName $spec.printer -ErrorAction SilentlyContinue | Where-Object { $_.DocumentName -eq $spec.document_name } | Select-Object -First 1
$printer = [string](Get-Printer -Name $spec.printer -ErrorAction SilentlyContinue).PrinterStatus
if ($job) { [pscustomobject]@{ in_queue = $true; spooler_id = [int]$job.Id; flags = [string]$job.JobStatus;
                               pages = [int]$job.TotalPages; printed = [int]$job.PagesPrinted; printer = $printer } | ConvertTo-Json -Compress }
else { [pscustomobject]@{ in_queue = $false; printer = $printer } | ConvertTo-Json -Compress }
"""

CANCEL = r"""
$ErrorActionPreference = 'Stop'
$spec = [Console]::In.ReadToEnd() | ConvertFrom-Json
$jobs = @(Get-PrintJob -PrinterName $spec.printer -ErrorAction SilentlyContinue | Where-Object { $_.DocumentName -eq $spec.document_name })
foreach ($j in $jobs) { Remove-PrintJob -InputObject $j }
[pscustomobject]@{ removed = $jobs.Count } | ConvertTo-Json -Compress
"""


REFUSALS   = frozenset({ "printer_not_found" })      # codes the script emits before anything is printed


def why( stderr: bytes ) -> str:
    """The first line of PowerShell's error, clipped: the message, never document content."""
    lines = [ l.strip() for l in stderr.decode( "utf-8", "replace" ).splitlines() if l.strip()]
    return f": { lines[ 0 ][ :160 ]}" if lines else ""


def printer_id_for( name: str ) -> str:
    slug = re.sub( r"[^a-z0-9]+", "_", name.lower()).strip( "_" )
    return ( slug or "printer" )[ :64 ]


def document_name( job_id: str ) -> str:
    return f"STB { job_id }"


def windows_path( path: Path ) -> str:
    """`/mnt/c/Users/x/f.png` -> `C:\\Users\\x\\f.png`. Anything not on a Windows drive is refused."""
    parts = path.resolve().parts
    if len( parts ) < 4 or parts[ 1 ] != "mnt" or not re.fullmatch( r"[a-z]", parts[ 2 ]):
        raise SpoolerRefused( "page images must live on a Windows drive (/mnt/<letter>/...)" )
    return str( PureWindowsPath( f"{ parts[ 2 ].upper()}:\\", *parts[ 3: ]))


def interpret( flags: str, in_queue: bool ) -> tuple[ JobState, str ]:
    """The spooler's words, as a job state. Pure. Never claims paper came out."""
    words = [ w.strip() for w in flags.split( "," ) if w.strip()]
    if not in_queue:
        return ( JobState.COMPLETED_PER_SPOOLER,
                 "the Windows queue released the job with no error reported; physical output is not verified" )
    if "Deleting" in words or "Deleted" in words:
        return JobState.CANCELED, "the spooler is deleting the job"
    if "Printed" in words or "Completed" in words:
        return JobState.COMPLETED_PER_SPOOLER, "the spooler reports the job printed; physical output is not verified"
    attention = [ w for w in words if w in NEEDS_HAND ]
    if attention:
        return JobState.SUBMITTED_TO_SPOOLER, f"in the queue and needs attention at the printer: { ', '.join( attention )}"
    return JobState.SUBMITTED_TO_SPOOLER, f"in the Windows queue ({ ', '.join( words ) or 'waiting' })"


class PowerShell:
    def __init__( self, runner: ICommandRunner, exe: str = POWERSHELL ) -> None:
        self._runner = runner
        self._exe    = exe

    def call( self, script: str, spec: dict, timeout: float ) -> dict:
        encoded = base64.b64encode( script.encode( "utf-16-le" )).decode( "ascii" )
        result  = self._runner.run([ self._exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                     "-EncodedCommand", encoded ], timeout=timeout,
                                   stdin=json.dumps( spec ).encode( "utf-8" ))
        text = result.stdout.decode( "utf-8", "replace" ).strip().splitlines()
        last = text[ -1 ] if text else ""
        try:
            body = json.loads( last ) if last else {}
        except ValueError:
            body = {}
        if result.returncode == 124:
            raise SpoolerError( "powershell_timeout" )
        if result.returncode != 0 or not isinstance( body, dict ):
            code = body.get( "error" ) if isinstance( body, dict ) else None
            if code in REFUSALS:
                raise SpoolerRefused( str( code ))
            raise SpoolerError( str( code or f"powershell_exit_{ result.returncode }{ why( result.stderr )}" ))
        return body


class WindowsPrinterDiscovery:
    def __init__( self, shell: PowerShell ) -> None:
        self._shell = shell

    def list_printers( self ) -> list[ PrinterInfo ]:
        body = self._shell.call( DISCOVER, {}, timeout=60.0 )
        raw  = body.get( "printers" ) or []
        if isinstance( raw, dict ):
            raw = [ raw ]
        found: list[ PrinterInfo ] = []
        for item in raw:
            name = str( item.get( "name", "" ))
            if not name or HIDDEN.search( name ) or HIDDEN.search( str( item.get( "port", "" ))):
                continue
            found.append( PrinterInfo( printer_id=printer_id_for( name ), name=name, driver=str( item.get( "driver", "" )),
                                       is_default=bool( item.get( "is_default" )), status=str( item.get( "status", "" )),
                                       shared=bool( item.get( "shared" ))))
        return found


class WindowsSpoolerBackend:
    """IPrintBackend. Page images are written to a private folder on C: and removed after submission."""

    def __init__( self, shell: PowerShell, spool_root: Path, submit_timeout: float = 300.0 ) -> None:
        self._shell   = shell
        self._root    = spool_root
        self._timeout = submit_timeout

    def submit( self, job: PrintJob ) -> PrintResult:
        work = self._root / job.job_id
        work.mkdir( mode=0o700, parents=True, exist_ok=False )
        try:
            pages: list[ str ] = []
            for n, page in enumerate( job.rendered.pages, start=1 ):
                path = work / f"page_{ n:03d}.png"
                path.write_bytes( page.png )
                pages.append( windows_path( path ))
            body = self._shell.call( SUBMIT, { "printer": job.printer.name, "document_name": document_name( job.job_id ),
                                               "copies": job.options.copies,
                                               "landscape": job.options.orientation == "landscape", "pages": pages },
                                     timeout=self._timeout )
        finally:
            shutil.rmtree( work, ignore_errors=True )
        spooler_id = body.get( "spooler_id" )
        state, detail = interpret( str( body.get( "flags", "" )), spooler_id is not None )
        if state is JobState.COMPLETED_PER_SPOOLER and spooler_id is None:
            state, detail = JobState.SUBMITTED_TO_SPOOLER, "handed to the Windows queue; it left the queue at once"
        return PrintResult( state=state, spooler_id=spooler_id if isinstance( spooler_id, int ) else None, detail=detail )

    def status( self, job_id: str, printer: PrinterInfo ) -> PrintStatus:
        body  = self._shell.call( STATUS, { "printer": printer.name, "document_name": document_name( job_id )}, timeout=60.0 )
        in_queue = bool( body.get( "in_queue" ))
        flags = str( body.get( "flags", "" ))
        state, detail = interpret( flags, in_queue )
        if body.get( "printer" ):
            detail += f"; printer status { body[ 'printer' ]}"
        return PrintStatus( state=state, in_queue=in_queue, flags=[ w.strip() for w in flags.split( "," ) if w.strip()],
                            pages=body.get( "pages" ) if isinstance( body.get( "pages" ), int ) else None, detail=detail )

    def cancel( self, job_id: str, printer: PrinterInfo ) -> PrintResult:
        body    = self._shell.call( CANCEL, { "printer": printer.name, "document_name": document_name( job_id )}, timeout=60.0 )
        removed = int( body.get( "removed" ) or 0 )
        if removed:
            return PrintResult( state=JobState.CANCELED, detail="removed from the Windows queue; pages already sent may still print" )
        return PrintResult( state=JobState.FAILED, detail="the job was not in the Windows queue, so there was nothing to cancel" )
