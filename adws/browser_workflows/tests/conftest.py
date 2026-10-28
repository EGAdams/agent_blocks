import sys
from pathlib import Path

ADWS_DIR = Path( __file__ ).resolve().parents[ 2 ]
if str( ADWS_DIR ) not in sys.path:
    sys.path.insert( 0, str( ADWS_DIR ))

COMMS_DIR = ADWS_DIR.parent / "lancedb_memory"         # agent_comms' IOperation / OperationOutcome
if str( COMMS_DIR ) not in sys.path:
    sys.path.insert( 1, str( COMMS_DIR ))
