"""AndroidPhotosAgent and its uv command adapter, offline. No phone, no Windows, no gateway.

    cd lancedb_memory
    .venv/bin/python -m agent_bridge_worker.tests.test_android_photos_agent
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from agent_bridge_worker.adapters.android_photos_process import UvAndroidPhotoCommands
from agent_bridge_worker.android_photos_agent import AndroidPhotosAgent
from agent_bridge_worker.models import OutcomeKind
from agent_bridge_worker.tests.test_deploy_agent import FakeCommands, run, task
from agent_bridge_worker.tests.test_bridge_worker import TASK_ID
from agent_comms.tests.fakes import FakeClock


class Commands( FakeCommands ):
    def start( self, operation, flags ):
        return super().start( operation, flags, False )


class Publisher:
    """IDownloadPublisher: gives every file an id, or refuses the lot."""

    def __init__( self, problem: str = "" ) -> None:
        self.problem, self.seen = problem, []

    def publish( self, task, details ):
        self.seen.append( details )
        files = [{ k: v for k, v in f.items() if k != "inspection" } for f in details[ "files" ]]
        if self.problem:
            return { **details, "files": files, "inspectable": False, "inspect_problem": self.problem }
        return { **details, "files": [{ **f, "file_id": f"f_{ f[ 'number' ]:024d}", "original_stored": True } for f in files ],
                 "inspectable": True }


def photo( n: int ) -> dict:
    return { "number": n, "name": f"IMG_2026111{ n }_101500.jpg", "folder": "DCIM/Camera", "storage": "Internal storage",
             "modified": "", "taken_at": f"2026-11-1{ n }T10:15:00", "taken_source": "exif", "bytes": 4000 + n }


def file( n: int ) -> dict:
    return { "name": photo( n )[ "name" ], "original_name": photo( n )[ "name" ], "path": f"/home/x/cache/{ n }.preview.jpg",
             "mime": "image/jpeg", "bytes": 900, "sha256": "ab" * 32, "number": n,
             "inspection": { "kind": "image", "page_count": 1, "text": "", "pages": [], "note": "thumbnail" }}


def ans( ok=True, line="The 2 newest photo(s) on Pixel 8, newest first.", **data ) -> dict:
    return { "ok": ok, "verified": ok, "retryable": False, "line": line, "data": data, "adw_id": "0badc0de" }


def listing() -> dict:
    return ans( android="list_recent_photos", state="ready", device="Pixel 8", total_on_phone=812,
                photos=[ photo( 1 ), photo( 2 )], files=[ file( 1 ), file( 2 )])


def test_a_listing_is_published_and_each_photo_gets_its_file_id():
    c, p = Commands( listing()), Publisher()
    out = run( AndroidPhotosAgent( c, FakeClock(), p ), task({ "android": "list_recent_photos", "count": 15 }, "inspect" ))
    assert out.kind == OutcomeKind.SUCCEEDED and out.details[ "verified" ] and out.details[ "inspectable" ]
    assert [ x[ "file_id" ] for x in out.details[ "photos" ]] == [ f"f_{ 1:024d}", f"f_{ 2:024d}" ]
    op, flags, _ = c.calls[ 0 ]
    assert op == "list_recent_photos" and flags == { "count": "15", "task-id": TASK_ID, "client": "eg-chatgpt" }
    assert len( p.seen[ 0 ][ "files" ]) == 2 and out.details[ "total_on_phone" ] == 812


def test_no_local_path_leaves_the_machine_in_the_task_record():
    out = run( AndroidPhotosAgent( Commands( listing()), FakeClock(), Publisher()), task({ "android": "list_recent_photos" }, "inspect" ))
    assert "/home/x" not in str( out.details ) and "/home/x" not in out.body and "path" not in out.details[ "files" ][ 0 ]


def test_status_publishes_nothing_and_a_phone_that_is_not_ready_says_what_to_do():
    p = Publisher()
    ready = ans( line="Pixel 8 is connected", android="status", state="ready", device="Pixel 8", photo_count=812 )
    out = run( AndroidPhotosAgent( Commands( ready ), FakeClock(), p ), task({ "android": "status" }, "inspect" ))
    assert out.kind == OutcomeKind.SUCCEEDED and out.details[ "photo_count" ] == 812 and p.seen == []
    locked = ans( ok=False, line="Pixel 8 is attached but shows no storage", android="status", state="locked",
                  next_step="Unlock the phone" )
    out = run( AndroidPhotosAgent( Commands( locked ), FakeClock(), p ), task({ "android": "list_recent_photos" }, "inspect" ))
    assert out.kind == OutcomeKind.FAILED and out.details[ "state" ] == "locked" and out.details[ "next_step" ] == "Unlock the phone"
    assert p.seen == []


def test_pictures_that_cannot_be_published_are_a_failure_not_a_success():
    for publisher in ( Publisher( "the gateway refused the manifest" ), None ):
        out = run( AndroidPhotosAgent( Commands( listing()), FakeClock(), publisher ), task({ "android": "list_recent_photos" }, "inspect" ))
        assert out.kind == OutcomeKind.FAILED and not out.details[ "verified" ] and out.details[ "inspect_problem" ]
        assert out.retryable


def test_one_photo_by_number_or_by_name_but_not_both():
    c = Commands( ans( line="IMG.jpg, taken 2026-11-11 at 10:15:00.", android="get_photo", state="ready", photos=[ photo( 1 )],
                       files=[ file( 1 )]))
    agent = AndroidPhotosAgent( c, FakeClock(), Publisher())
    assert run( agent, task({ "android": "get_photo", "number": 3 }, "inspect" )).kind == OutcomeKind.SUCCEEDED
    assert run( agent, task({ "android": "get_thumbnail", "name": " IMG_20261114_093015.jpg " }, "inspect" )).kind == OutcomeKind.SUCCEEDED
    assert c.calls[ 0 ][ 1 ][ "number" ] == "3" and c.calls[ 1 ][ 1 ][ "name" ] == "IMG_20261114_093015.jpg"
    assert run( agent, task({ "android": "get_photo", "number": 3, "name": "a.jpg" }, "inspect" )).kind == OutcomeKind.REFUSED
    assert run( agent, task({ "android": "get_photo" }, "inspect" )).kind == OutcomeKind.REFUSED


def test_nothing_but_reading_and_bad_values_never_reach_the_adw():
    c = Commands( listing())
    agent = AndroidPhotosAgent( c, FakeClock(), Publisher())
    for bad, op in (({ "android": "status" }, "implement" ),
                    ({ "android": "delete_photo", "number": 1 }, "inspect" ),
                    ({ "android": "delete_photo", "number": 1 }, "implement" ),
                    ({ "android": "list_recent_photos", "count": 0 }, "inspect" ),
                    ({ "android": "list_recent_photos", "count": 500 }, "inspect" ),
                    ({ "android": "list_recent_photos", "count": "15; rm" }, "inspect" ),
                    ({ "android": "list_recent_photos", "count": True }, "inspect" ),
                    ({ "android": "list_recent_photos", "folder": "Download" }, "inspect" ),
                    ({ "android": "list_recent_photos", "device": "Other Phone" }, "inspect" ),
                    ({ "android": "status", "count": 3 }, "inspect" ),
                    ({ "android": "get_photo", "name": "../../secret.jpg" }, "inspect" ),
                    ({ "android": "get_photo", "name": "C:\\Users\\x.jpg" }, "inspect" ),
                    ({ "android": "get_photo", "name": "--json-out=/x" }, "inspect" ),
                    ({ "android": "get_photo", "name": "x" * 181 }, "inspect" ),
                    ({ "android": "get_photo", "number": 21 }, "inspect" ),
                    ({ "android": "get_photo", "number": 1, "path": "/etc" }, "inspect" ),
                    ( "not json", "inspect" )):
        assert run( agent, task( bad, op )).kind == OutcomeKind.REFUSED, bad
    assert c.calls == []


def test_no_outcome_from_the_adw_is_a_retryable_failure():
    out = run( AndroidPhotosAgent( Commands( error="exit 1" ), FakeClock(), Publisher()), task({ "android": "status" }, "inspect" ))
    assert out.kind == OutcomeKind.FAILED and out.retryable and out.details == { "android": "status", "ok": False, "verified": False }


def test_a_cancel_or_a_lost_lease_stops_the_read():
    for signal, kind in (( "cancel", OutcomeKind.CANCELLED ), ( "lost", OutcomeKind.LEASE_LOST )):
        c = Commands( listing(), ticks=5 )
        out = run( AndroidPhotosAgent( c, FakeClock(), Publisher()), task({ "android": "list_recent_photos" }, "inspect" ),
                   [ "continue", signal ])
        assert out.kind == kind


def test_the_command_line_is_the_one_adw_with_flag_equals_value():
    argv = UvAndroidPhotoCommands( Path( "/usr/bin/uv" ), Path( "/x/adw_android_photos.py" ), Path( "/x" )).argv(
        "get_photo", { "name": "-rf.jpg", "task-id": "t1" }, "/tmp/res" )
    assert argv == [ "/usr/bin/uv", "run", "--quiet", "/x/adw_android_photos.py", "get_photo", "--name=-rf.jpg", "--task-id=t1",
                     "--json-out=/tmp/res" ]


if __name__ == "__main__":
    tests = [ v for k, v in sorted( globals().items()) if k.startswith( "test_" )]
    for t in tests:
        t()
        print( "ok  ", t.__name__ )
    print( f"{ len( tests ) } passed" )
