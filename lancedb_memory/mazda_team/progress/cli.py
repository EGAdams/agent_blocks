"""Print Michi's deterministic progress snapshot with no LLM call.

    .venv/bin/python -m mazda_team.progress.cli
"""
import argparse

from .. import specialist_roles
from . import tracker


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=specialist_roles.REPO_ROOT)
    args = parser.parse_args()
    snapshot = tracker.collect_and_persist(args.repo_root)
    print(snapshot.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
