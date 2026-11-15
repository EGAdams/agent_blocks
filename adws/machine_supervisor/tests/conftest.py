"""Puts adws/ on the path (machine_supervisor's own __init__ adds lancedb_memory/ for agent_comms)."""
import sys
from pathlib import Path

_ADWS = str(Path(__file__).resolve().parents[2])
if _ADWS not in sys.path:
    sys.path.insert(0, _ADWS)
