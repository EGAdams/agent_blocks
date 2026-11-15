"""Machine Supervisor: an agent-free cron watch on each machine, and a supervisor agent that sleeps until the
watch finds something wrong. Read DESIGN.md first.

agent_comms (Swarm-Forge's handoff mailboxes in Python) lives in lancedb_memory/, which has no package root,
so it is put on the path here once, the way post_office_adw.py does it.
"""
import sys
from pathlib import Path

_COMMS = str(Path(__file__).resolve().parents[2] / "lancedb_memory")
if _COMMS not in sys.path:
    sys.path.insert(1, _COMMS)
