"""Puts adws/ on the path."""
import sys
from pathlib import Path

_ADWS = str(Path(__file__).resolve().parents[2])
if _ADWS not in sys.path:
    sys.path.insert(0, _ADWS)
