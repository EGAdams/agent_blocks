"""Print Michi's deterministic progress snapshot with no LLM call.

    cd lancedb_memory
    .venv/bin/python -m receptionist_team.progress.cli
"""
import argparse

from . import michi, tracker


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=michi.REPO_ROOT)
    args = parser.parse_args()
    snapshot = tracker.collect_and_persist(args.repo_root)
    print(snapshot.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
