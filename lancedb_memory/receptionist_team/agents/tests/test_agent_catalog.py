"""The agent folders: discovery, loading, rendering, and the role mapping.

Offline -- reads only the folders under receptionist_team/agents/. No LanceDB,
no Groq, no Claude, no torch. Run from lancedb_memory:

    .venv/bin/python -m receptionist_team.agents.tests.test_agent_catalog

This file follows the SSSF bracket-spacing convention because the whole
agents/ subtree does; the rest of receptionist_team/tests/ does not. Match the
file you are editing (see ../../CLAUDE.md).
"""
import tempfile
from pathlib import Path

from agent_memory.prompts import DEFAULT_TEMPLATE

from .. import AgentRoleFactory, IncompleteAgentDefinition, build_catalog, catalog
from ..catalog import UnknownAgent
from ..layout import PART_DIRS, DirectoryAgentLayout
from ..models import ModelConfiguration
from ..prompt_templates import MissingPlaceholderValue, TextPromptTemplate


async def test_the_catalog_finds_every_agent_folder_and_nothing_else():
    names = catalog().names()
    assert "toyota" in names
    assert "spreadsheet_reshaper" in names
    assert "toyota_mechanic" in names
    # Its own .py files and __pycache__ are not agents.
    assert not any( name.endswith( ".py" ) or name.startswith( "__" ) for name in names )


async def test_an_unknown_agent_names_the_ones_that_exist():
    try:
        catalog().definition( "nissan" )
    except UnknownAgent as error:
        assert "nissan" in str( error ) and "toyota" in str( error )
    else:
        raise AssertionError( "expected UnknownAgent" )


async def test_toyotas_system_prompt_is_the_folder_rendered_with_the_roster():
    # The import-time render in roles.py is the real consumer; this asserts the
    # template on disk is what produces it.
    from ...roles import TOYOTA_ROLES, TOYOTA_SYSTEM_PROMPT

    template = catalog().definition( "toyota" ).system_prompt()
    assert set( template.placeholders()) == { "roster", "allowed_agents" }
    assert "{{" not in TOYOTA_SYSTEM_PROMPT
    # Every routable role is named in the rendered roster and allow-list.
    for name in TOYOTA_ROLES:
        assert name in TOYOTA_SYSTEM_PROMPT
    # Behaviour the prompt has always promised, now sourced from the folder.
    assert "ROUTE: <agent_name>" in TOYOTA_SYSTEM_PROMPT
    assert "OPEN_AGENT: mazda" in TOYOTA_SYSTEM_PROMPT
    # The mechanic is Sydney's to summon now, for both of them.
    assert "OPEN_AGENT: sydney" in TOYOTA_SYSTEM_PROMPT


async def test_sydneys_system_prompt_is_her_folder_rendered_with_the_rest_of_the_roster():
    from ...roles import ROLES, TOYOTA_ROLES
    from ...sydney_roles import SYDNEY_ROLES, SYDNEY_SYSTEM_PROMPT

    template = catalog().definition( "sydney" ).system_prompt()
    assert set( template.placeholders()) == { "roster", "allowed_agents" }
    assert "{{" not in SYDNEY_SYSTEM_PROMPT
    # The two rosters split ROLES between them with nothing left over.
    assert set( SYDNEY_ROLES ) | set( TOYOTA_ROLES ) == set( ROLES )
    assert not set( SYDNEY_ROLES ) & set( TOYOTA_ROLES )
    for name in SYDNEY_ROLES:
        assert f"ROUTE: { name }" in SYDNEY_SYSTEM_PROMPT, name
    assert "OPEN_AGENT: toyota" in SYDNEY_SYSTEM_PROMPT
    assert catalog().definition( "sydney" ).user_prompt().raw() == DEFAULT_TEMPLATE


async def test_toyotas_user_prompt_file_is_byte_identical_to_the_engine_default():
    # main.py now builds TemplatePromptComposer from this file instead of
    # letting it default, so the refactor is only behaviour-preserving while
    # the two are equal. If the file is edited on purpose, update this test
    # in the same commit -- that is the point of pinning it.
    assert catalog().definition( "toyota" ).user_prompt().raw() == DEFAULT_TEMPLATE


async def test_a_placeholder_with_no_value_fails_loud():
    template = TextPromptTemplate( "hello {{who}}", origin="test" )
    try:
        template.render({})
    except MissingPlaceholderValue as error:
        assert "who" in str( error )
    else:
        raise AssertionError( "expected MissingPlaceholderValue" )


async def test_rendering_leaves_the_engines_own_delimiters_alone():
    # A user prompt template is handed to TemplatePromptComposer unrendered;
    # the two delimiter styles must not collide.
    rendered = TextPromptTemplate( "{{a}} <<MESSAGE>>" ).render({ "a": "x" })
    assert rendered == "x <<MESSAGE>>"


async def test_the_reshaper_folder_produces_the_role_it_used_to_hardcode():
    role = AgentRoleFactory().role( catalog().definition( "spreadsheet_reshaper" ))
    assert role.name == "spreadsheet_reshaper"
    assert role.max_turns == 20
    assert role.output_path.endswith( "/readable_documents/reports" )
    assert set( role.allowed_tools ) == { "Bash", "Read", "Write", "Glob", "Grep" }
    # The two guarantees its prompts have always made.
    assert "never write to" in role.system_prompt or "Never overwrite" in role.system_prompt
    assert "_by_person" in role.task_prompt


async def test_the_registered_role_is_the_one_routing_actually_dispatches():
    from ...roles import ROLES

    assert ROLES[ "spreadsheet_reshaper" ].task_prompt == (
        catalog().definition( "spreadsheet_reshaper" ).user_prompt().raw()
    )


async def test_a_folder_with_no_output_path_cannot_become_a_role():
    # toyota is a conversationalist, not a delegated role: no output_path, so
    # asking for a role out of her folder must say so instead of inventing one.
    try:
        AgentRoleFactory().role( catalog().definition( "toyota" ))
    except IncompleteAgentDefinition as error:
        assert "output_path" in str( error )
    else:
        raise AssertionError( "expected IncompleteAgentDefinition" )


async def test_tool_folders_report_themselves_as_stubs():
    tools = { tool.name: tool for tool in catalog().definition( "toyota" ).tools() }
    assert set( tools ) == { "bank_login", "web_search" }
    assert all( tool.status == "stub" for tool in tools.values())


async def test_a_missing_model_json_loads_as_defaults_rather_than_failing():
    # Was toyota_mechanic's folder until its migration finished; a scratch
    # folder keeps the behaviour pinned without depending on any real agent.
    with tempfile.TemporaryDirectory() as root:
        ( Path( root ) / "unconfigured" / "system_prompt_template" ).mkdir( parents=True )
        definition = build_catalog( root=Path( root )).definition( "unconfigured" )
        assert definition.model_configuration() == ModelConfiguration()


async def test_an_empty_directory_is_a_catalog_with_no_agents():
    empty = build_catalog( root=Path( __file__ ).resolve().parent / "no_such_dir" )
    assert empty.names() == ()


async def test_the_layout_is_the_only_place_folder_names_are_spelled():
    layout = DirectoryAgentLayout()
    root = Path( "/tmp/agent" )
    parts = {
        layout.system_prompt_dir( root ).name,
        layout.user_prompt_dir( root ).name,
        layout.model_configuration_dir( root ).name,
        layout.tools_dir( root ).name,
        layout.documentation_dir( root ).name,
    }
    assert parts == set( PART_DIRS )


if __name__ == "__main__":
    from tests.runner import main
    main( dict( globals()))
