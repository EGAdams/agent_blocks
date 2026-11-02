"""Offline tests for the company to-do list. Run from lancedb_memory/:

    .venv/bin/python -m company_todos.tests.test_company_todos
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from company_todos.desk import TodoDesk
from company_todos.models import TodoAction, TodoState
from company_todos.requests import read_request
from company_todos.store import InMemoryTodoStore, JsonlTodoStore

ADW = Path( __file__ ).resolve().parents[ 3 ] / "adws" / "adw_interface_company_todos.py"


def say( desk: TodoDesk, text: str ) -> str:
    return desk.handle( read_request( text ))


async def test_reads_add_list_and_done():
    cases = {
        "Add renew the church insurance to the to-do list": ( TodoAction.ADD, "renew the church insurance" ),
        "put call the roofer on our company todo list please": ( TodoAction.ADD, "call the roofer" ),
        "to-do: order toner": ( TodoAction.ADD, "order toner" ),
        "add to the to-do list: file the PRE form": ( TodoAction.ADD, "file the PRE form" ),
        "what's on the to-do list?": ( TodoAction.LIST, "" ),
        "read me the todo list": ( TodoAction.LIST, "" ),
    }
    for sentence, ( action, text ) in cases.items():
        command = read_request( sentence )
        assert ( command.action, command.text ) == ( action, text ), ( sentence, command )
    assert read_request( "mark number 3 done on the to-do list" ).number == 3
    assert read_request( "check off #2 on the todo list" ).number == 2
    assert read_request( "the roofer is done, cross it off the to-do list" ).text == "roofer"


async def test_add_with_a_done_word_is_still_an_add():
    command = read_request( "add finish the taxes to the to-do list" )
    assert command.action is TodoAction.ADD and command.text == "finish the taxes"


async def test_unclear_requests_change_nothing():
    desk = TodoDesk( InMemoryTodoStore())
    for sentence in ( "don't add anything to the to-do list",
                      "how do I use the to-do list?",
                      "add to the to-do list" ):
        line = say( desk, sentence )
        assert line.startswith( "I didn't change" ), ( sentence, line )
    assert desk.open_items() == []


async def test_add_list_done_round_trip():
    desk = TodoDesk( InMemoryTodoStore())
    assert "nothing open" in say( desk, "what's on the to-do list" )
    assert "#1 call the roofer" in say( desk, "add call the roofer to the to-do list" )
    assert "#2 order toner" in say( desk, "add order toner to the to-do list" )
    assert "already on the company to-do list as #1" in say( desk, "add Call the roofer to the to-do list" )
    listed = say( desk, "what's on the to-do list" )
    assert "2 open items: #1 call the roofer; #2 order toner." in listed
    assert "Checked off #1 call the roofer" in say( desk, "the roofer is done, cross it off the to-do list" )
    assert "already checked off" in say( desk, "mark number 1 done on the to-do list" )
    assert "no item #9" in say( desk, "mark number 9 done on the to-do list" )
    assert "(1 done.)" in say( desk, "show the to-do list" )


async def test_ambiguous_done_changes_nothing():
    desk = TodoDesk( InMemoryTodoStore())
    say( desk, "add call the roofer about the leak to the to-do list" )
    say( desk, "add call the roofer for a quote to the to-do list" )
    line = say( desk, "the roofer is done, cross it off the to-do list" )
    assert line.startswith( "More than one item matches" ), line
    assert len( desk.open_items()) == 2


async def test_jsonl_store_survives_restart_keeps_history_and_is_private():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path( tmp ) / "todos"
        first = JsonlTodoStore( home )
        first.add( "call the roofer", "toyota" )
        first.complete( 1, "toyota" )
        again = JsonlTodoStore( home )
        [ item ] = again.items()
        assert item.state is TodoState.DONE and item.done_by == "toyota"
        assert len( again.path.read_text().splitlines()) == 2        # both versions kept
        assert stat.S_IMODE( again.path.stat().st_mode ) == 0o600
        assert stat.S_IMODE( home.stat().st_mode ) == 0o700


async def test_adw_writes_one_line():
    with tempfile.TemporaryDirectory() as tmp:
        line_file = Path( tmp ) / "line.txt"
        env = { **os.environ, "COMPANY_TODOS_HOME": str( Path( tmp ) / "todos" ),
                "AGENT_POST_OFFICE_ROOT": tmp }
        done = subprocess.run(
            [ sys.executable, str( ADW ), "test", "--request", "add order toner to the to-do list",
              "--message-out", str( line_file )],
            cwd=ADW.parents[ 1 ], env=env, capture_output=True, text=True, timeout=60 )
        assert done.returncode == 0, done.stderr[ -800: ]
        assert line_file.read_text().strip() == "Added to the company to-do list as #1 order toner. 1 open now."


if __name__ == "__main__":
    from tests.runner import main
    main( globals())
