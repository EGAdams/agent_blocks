"""Configuration. The one place absolute paths are allowed to be decided."""
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

PACKAGE_ROOT = Path( __file__ ).resolve().parent.parent

EmbeddingProfileName = Literal[ "hash", "sentence", "openai" ]


class Settings( BaseModel ):
    """Every path defaults inside this repo -- nothing points at a home directory."""

    session_id: str = "default"
    model: str = "claude-haiku-4-5-20251001"

    # "hash" (free, quick tests), "sentence" (free, local semantic model, for
    # "does this feel like it remembers" chats), "openai" (costs money, best
    # quality). Each profile owns its own table -- see adapters/embedding_profiles.py.
    embedding_profile: EmbeddingProfileName = "hash"

    lancedb_uri: Path = PACKAGE_ROOT / "lance_data"
    table_name: str | None = None   # None -- derive from embedding_profile
    nexus_dir: Path = PACKAGE_ROOT / "nexus"
    prompt_template_path: Path = PACKAGE_ROOT / "prompt_response.txt"

    recall_limit: int = 30
    recall_max_chars: int = 12000
    session_scoped_recall: bool = True
    # In a long-lived session, old turns that happen to share wording with the
    # current message can fill every similarity slot and crowd out what was
    # actually just said. This guarantees the last N turns always make it in.
    recall_recency_floor: int = 10
