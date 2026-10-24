"""The category-spreadsheet specialist: the folder role, and the ADW it names.

The role used to live in finance_team/roles.py pointing at rol_finances'
adws/adw_category_spreadsheet.py. That script is retired; this package's
EXTRA_ROLE_NAMES entry overrides the finance_team one so the name now
resolves to agents/category_spreadsheet/, which runs this workspace's
adws/adw_interface_category_spreadsheet.py instead.

    cd lancedb_memory
    .venv/bin/python -m receptionist_team.tests.test_category_spreadsheet_role
"""
from finance_team.roles import ROLES as FINANCE_ROLES

from ..roles import ROLE_BLURBS, ROLES, TOYOTA_SYSTEM_PROMPT, delegation_acknowledgment


async def test_the_role_runs_this_workspaces_adw_with_only_bash_and_read():
    role = ROLES['category_spreadsheet']
    assert 'adw_interface_category_spreadsheet.py' in role.task_prompt
    assert set(role.allowed_tools) == {'Bash', 'Read'}


async def test_the_folder_role_wins_over_the_retired_finance_team_one():
    # Both names exist; the merged dict must resolve to the folder, or
    # Toyota would silently go on running the retired script.
    assert 'adw_category_spreadsheet.py' in FINANCE_ROLES['category_spreadsheet'].task_prompt
    assert 'adw_category_spreadsheet.py' not in ROLES['category_spreadsheet'].task_prompt


async def test_the_role_relays_a_choice_instead_of_guessing_the_category():
    prompt = ROLES['category_spreadsheet'].task_prompt
    assert '--category-id' in prompt
    assert 'do not' in prompt.lower() and 'ask which' in prompt.lower()


async def test_the_role_knows_about_compare_and_the_medical_group():
    prompt = ROLES['category_spreadsheet'].task_prompt
    assert '--compare' in prompt and '--year' in prompt
    assert 'Medical' in prompt


async def test_the_system_prompt_still_refuses_to_invent_or_change_a_figure():
    system = ROLES['category_spreadsheet'].system_prompt
    assert 'Never put a figure' in system
    assert 'worth a look' in system and 'never "wrong"' in system


async def test_toyota_knows_when_to_route_to_it():
    assert 'category_spreadsheet' in TOYOTA_SYSTEM_PROMPT
    assert 'ROUTE: category_spreadsheet |' in TOYOTA_SYSTEM_PROMPT
    assert 'category_spreadsheet' in ROLE_BLURBS
    assert 'category_spreadsheet' not in delegation_acknowledgment('category_spreadsheet')


if __name__ == '__main__':
    from tests.runner import main
    main(dict(globals()))
