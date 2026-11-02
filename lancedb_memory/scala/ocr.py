"""IOcrReader over the tesseract command line: the cheap look.

The command, not pytesseract, because the receptionists' venv has neither
pytesseract nor Pillow and the binary is already on this box. A PDF goes
through `pdftotext` instead (its text layer, first two pages). `--psm 1` is
automatic layout *with* orientation detection, so a page fed in upside down
or sideways is read the right way up (the default mode reads it as garbage).
Never raises:
a missing binary, a timeout or a bad image all come back as "".
"""
from __future__ import annotations

import subprocess
from pathlib import Path


class TesseractOcr:
    def __init__( self, timeout_sec: float = 90.0 ) -> None:
        self._timeout = timeout_sec

    def read( self, image: Path ) -> str:
        if image.suffix.lower() == ".pdf":
            command = [ "pdftotext", "-l", "2", "-layout", str( image ), "-" ]
        else:
            command = [ "tesseract", str( image ), "stdout", "--psm", "1" ]
        try:
            proc = subprocess.run( command, capture_output=True, text=True, timeout=self._timeout )
        except ( OSError, subprocess.TimeoutExpired ):
            return ""
        return proc.stdout if proc.returncode == 0 else ""
