"""The category-spreadsheet agent: a routed role whose only job is to run one ADW."""
from finance_team.roles import ROLES
from ..roles import ROLE_BLURBS, TOYOTA_SYSTEM_PROMPT, delegation_acknowledgment


async def test_the_role_exists_and_runs_the_adw_with_only_bash_and_read():
    role = ROLES['category_spreadsheet']
    assert 'adws/adw_category_spreadsheet.py' in role.task_prompt
    assert set(role.allowed_tools) == {'Bash', 'Read'}


async def test_the_role_relays_a_choice_instead_of_guessing_and_reports_the_file():
    prompt = ROLES['category_spreadsheet'].task_prompt
    assert 'NEEDS_CHOICE' in prompt and '--category-id' in prompt and 'FILE:' in prompt


async def test_toyota_knows_when_to_route_to_it():
    assert 'category_spreadsheet' in TOYOTA_SYSTEM_PROMPT
    assert 'ROUTE: category_spreadsheet |' in TOYOTA_SYSTEM_PROMPT
    assert 'category_spreadsheet' in ROLE_BLURBS
    assert 'category_spreadsheet' not in delegation_acknowledgment('category_spreadsheet')


if __name__ == '__main__':
    from tests.runner import main
    main(dict(globals()))
