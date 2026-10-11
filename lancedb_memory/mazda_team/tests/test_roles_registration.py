"""Mazda's progress agent registration and prompt tests."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import mazda_team.roles as roles


async def test_all_agent_names_includes_michi():
    assert "michi" in roles.ALL_AGENT_NAMES


async def test_import_succeeds_meaning_completeness_dicts_cover_michi():
    assert all("michi" in mapping for mapping in
               (roles.ROLE_BLURBS, roles.ROLE_SPOKEN_NAMES, roles.ROLE_START_TASKS))


async def test_delegation_acknowledgment_for_michi():
    assert roles.ROLE_SPOKEN_NAMES["michi"] in roles.delegation_acknowledgment("michi")


async def test_system_prompt_routes_progress_questions_to_michi():
    assert "ROUTE: michi" in roles.MAZDA_SYSTEM_PROMPT
    assert "michi" in next(line for line in roles.MAZDA_SYSTEM_PROMPT.splitlines()
                           if line.startswith("Allowed agent names:"))


if __name__ == "__main__":
    from mazda_team.tests.runner import main
    main(globals())
