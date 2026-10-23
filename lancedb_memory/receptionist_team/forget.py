"""Find and delete stored turns a receptionist got wrong.

Why this exists: recall hands a stored turn back as history. Mazda once
answered a status question from memory instead of routing to Mickey and said a
year of finance reports was complete when none existed -- and that reply was
then stored, recalled every turn, and re-asserted, each time looking more
like established fact. A prompt rule cannot outargue forty turns of apparent
history. The only way out is to delete the wrong turns.

Shows what it matched and changes nothing unless you pass --apply. Deletes
from the LanceDB table (the read path, so this is what stops the recall) and
from the nexus archive file of the same id, so the fabrication isn't left
lying around in the write-only copy either.

    .venv/bin/python -m receptionist_team.forget --agent mazda \
        --match "all 2025.*(green|complete|verified)"
    .venv/bin/python -m receptionist_team.forget --agent mazda --match "..." --apply
"""
import argparse
import re
import sys

from agent_memory.adapters.embedding_profiles import build_embedder
from agent_memory.adapters.lancedb_store import LanceDBMemoryStore
from agent_memory.config import Settings

from .profiles import PROFILES, profile as named_profile


def _nexus_path(settings: Settings, turn_id: str):
    return settings.nexus_dir / f"{turn_id}.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", default="mazda", choices=sorted(PROFILES))
    parser.add_argument("--match", required=True,
                        help="case-insensitive regex; a turn whose message matches is a candidate")
    parser.add_argument("--speaker", default="AGENT", choices=["AGENT", "USER", "any"],
                        help="whose turns to consider (default AGENT: her own wrong answers)")
    parser.add_argument("--limit", type=int, default=500,
                        help="how many recent turns to scan")
    parser.add_argument("--embedding-profile", default="sentence",
                        choices=["hash", "sentence", "openai"])
    parser.add_argument("--keep", nargs="*", default=[], metavar="ID",
                        help=("turn ids to leave alone even though they match. A turn is "
                              "all-or-nothing here, so one that mixes a wrong claim with "
                              "real history is better kept than silently rewritten"))
    parser.add_argument("--apply", action="store_true",
                        help="actually delete; without this, nothing changes")
    args = parser.parse_args()

    profile = named_profile(args.agent)
    settings = Settings(session_id=profile.default_session,
                        embedding_profile=args.embedding_profile)
    settings = settings.model_copy(
        update={"nexus_dir": settings.nexus_dir / profile.nexus_subdir}
    )
    bundle = build_embedder(settings.embedding_profile)
    store = LanceDBMemoryStore(settings.lancedb_uri, bundle.table_name, bundle.embedder.dimensions)

    pattern = re.compile(args.match, re.IGNORECASE | re.DOTALL)
    hits = [
        turn for turn in store.recent(settings.session_id, args.limit)
        if (args.speaker == "any" or turn.speaker == args.speaker)
        and pattern.search(turn.message)
        and not any(str(turn.id).startswith(prefix) for prefix in args.keep)
    ]

    if not hits:
        print(f"No {profile.display_name} turns match {args.match!r}.")
        return 0

    print(f"{len(hits)} matching {profile.display_name} turn(s):\n")
    for turn in hits:
        first = " ".join(turn.message.split())[:140]
        print(f"  {turn.id}  {turn.speaker:<5}  {first}")

    if not args.apply:
        print(f"\nNothing deleted. Re-run with --apply to remove these {len(hits)}.")
        return 0

    removed = store.forget([str(turn.id) for turn in hits])
    archived = 0
    for turn in hits:
        path = _nexus_path(settings, str(turn.id))
        if path.exists():
            path.unlink()
            archived += 1
    print(f"\nDeleted {removed} row(s) from {bundle.table_name} "
          f"and {archived} nexus file(s).")
    print("Restart her service so the warm agent stops holding the old table handle:")
    print(f"  systemctl --user restart {profile.name}-receptionist.service")
    return 0


if __name__ == "__main__":
    sys.exit(main())
