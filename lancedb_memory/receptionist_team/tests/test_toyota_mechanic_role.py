"""The mechanic: a routed role whose only job is to run one ADW."""
from finance_team.roles import ROLES
from ..roles import ROLE_BLURBS, ROLE_SPOKEN_NAMES, TOYOTA_SYSTEM_PROMPT


async def test_the_role_runs_the_adw_with_only_bash_and_read():
    role = ROLES['toyota_mechanic']
    assert 'adws/adw_toyota_mechanic.py' in role.task_prompt
    assert set(role.allowed_tools) == {'Bash', 'Read'}


async def test_the_role_relays_every_outcome_and_never_overclaims():
    prompt = ROLES['toyota_mechanic'].task_prompt
    for marker in ('DIAGNOSIS:', 'DONE:', 'REFUSED:', 'BUSY:', '--diagnose'):
        assert marker in prompt


async def test_toyota_knows_to_route_self_repair_to_the_mechanic():
    assert 'toyota_mechanic' in ROLE_BLURBS and 'toyota_mechanic' in ROLE_SPOKEN_NAMES
    assert 'ROUTE: toyota_mechanic' in TOYOTA_SYSTEM_PROMPT


if __name__ == '__main__':
    from tests.runner import main
    main(dict(globals()))
