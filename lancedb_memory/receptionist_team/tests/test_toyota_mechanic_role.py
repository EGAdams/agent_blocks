"""The mechanic: a routed role whose only job is to run one ADW."""
from pathlib import Path

from ..roles import ROLE_BLURBS, ROLE_SPOKEN_NAMES, ROLES, TOYOTA_ROSTER, TOYOTA_SYSTEM_PROMPT
from ..sydney_roles import SYDNEY_ROSTER, SYDNEY_SYSTEM_PROMPT
from ..agents import catalog

AGENT_BLOCKS = Path(__file__).resolve().parents[3]


async def test_the_role_runs_the_adw_with_only_bash_and_read():
    role = ROLES['toyota_mechanic']
    assert 'adws/adw_toyota_mechanic.py' in role.task_prompt
    assert set(role.allowed_tools) == {'Bash', 'Read'}


async def test_the_role_is_defined_by_its_agent_folder_not_finance_team():
    from finance_team.roles import ROLES as FINANCE_ROLES
    assert 'toyota_mechanic' not in FINANCE_ROLES
    folder = catalog().definition('toyota_mechanic')
    assert ROLES['toyota_mechanic'].system_prompt == folder.system_prompt().raw()


async def test_the_workflow_it_names_is_in_agent_blocks():
    role = ROLES['toyota_mechanic']
    assert 'cd /home/adamsl/agent_blocks' in role.task_prompt
    # Inside a mechanic sandbox this is the adws/ mirror, which carries it too.
    assert (AGENT_BLOCKS / 'adws' / 'adw_toyota_mechanic.py').is_file()


async def test_the_role_relays_every_outcome_and_never_overclaims():
    prompt = ROLES['toyota_mechanic'].task_prompt
    for marker in ('DIAGNOSIS:', 'DONE:', 'REFUSED:', 'BUSY:', '--diagnose'):
        assert marker in prompt


async def test_the_coder_is_a_folder_but_never_routable():
    # Routed to directly, the coder would edit live code with no sandbox,
    # gate or rollback -- only adws/adw_toyota_mechanic.py may run it.
    assert 'mechanic_coder' in catalog().names()
    assert 'mechanic_coder' not in ROLES
    assert catalog().definition('mechanic_coder').model_configuration().model


async def test_sydney_summons_the_mechanic_for_both_receptionists():
    assert 'toyota_mechanic' in ROLE_BLURBS and 'toyota_mechanic' in ROLE_SPOKEN_NAMES
    assert SYDNEY_ROSTER.has('toyota_mechanic') and SYDNEY_ROSTER.has('mechanic_translator')
    assert 'ROUTE: toyota_mechanic' in SYDNEY_SYSTEM_PROMPT
    # Toyota no longer routes there herself: she opens Sydney's window.
    assert not TOYOTA_ROSTER.has('toyota_mechanic')
    assert 'ROUTE: toyota_mechanic' not in TOYOTA_SYSTEM_PROMPT
    assert 'OPEN_AGENT: sydney' in TOYOTA_SYSTEM_PROMPT


if __name__ == '__main__':
    from tests.runner import main
    main(dict(globals()))
