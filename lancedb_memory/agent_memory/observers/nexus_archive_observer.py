import json
from pathlib import Path

from ..models import AgentReply, EmbeddedTurn, Turn


class NexusArchiveObserver:
    """Writes one JSON file per turn into the nexus directory.

    This is a WRITE-ONLY archive: nothing in the read path depends on it, so a
    missing or emptied nexus can no longer silently degrade recall. Its purpose
    is portability -- the raw text survives independently of LanceDB, so the
    vector store can be swapped for one that does not retain message bodies.
    """

    def __init__( self, nexus_dir: Path ) -> None:
        self._nexus_dir = nexus_dir
        self._nexus_dir.mkdir( parents=True, exist_ok=True )

    def on_turn_complete(
        self,
        user_turn: EmbeddedTurn,
        agent_turn: EmbeddedTurn,
        recalled: list[ Turn ],
        reply: AgentReply,
    ) -> None:
        for embedded in ( user_turn, agent_turn ):
            self._archive( embedded )

    def _archive( self, embedded: EmbeddedTurn ) -> None:
        turn = embedded.turn
        payload = {
            "id": str( turn.id ),
            "session_id": turn.session_id,
            "speaker": turn.speaker,
            "message": turn.message,
            "time": turn.created_at.timestamp(),
            "timestring": turn.created_at.strftime( "%A, %B %d, %Y at %I:%M%p %Z" ),
            "vector": embedded.vector,
        }
        path = self._nexus_dir / f"{ turn.id }.json"
        with open( path, "w", encoding="utf-8" ) as outfile:
            json.dump( payload, outfile, ensure_ascii=False, sort_keys=True, indent=2 )
