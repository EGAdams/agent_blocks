"""Checks Construction Status pages for the drift described in
skills/construction-status-queue-management/SKILL.md: a task's status
disagreeing between the plan's hidden task tree and its lesson page, a
queue-table link that is broken or disagrees with its target's own status,
or a "next steps" queue table duplicated outside its owning plan file.

Stdlib only, regex-based -- kept dependency-free like doc_source_map.py, and
deliberately not a full HTML parse. The markup this reads is authored by
hand following a fixed shape (see building-the-spa/SKILL.md's "Construction
Status task trees" section); this script checks that shape's *content*
agrees with itself, not that the shape is well-formed HTML.

Run standalone:
    python3 check_construction_status_consistency.py
    python3 check_construction_status_consistency.py voice_communication/conversation_agent/basic_agent_construction_status.html
Exit status is 1 if any finding was reported, 0 otherwise.
"""
import re
import sys
from dataclasses import dataclass
from pathlib import Path

DOCS_ROOT = Path( __file__ ).resolve().parent


def _display( path: Path ) -> Path:
    """path.relative_to( DOCS_ROOT ), or path itself if it lies outside
    DOCS_ROOT (e.g. a file passed in for standalone/ad-hoc testing).
    """
    try:
        return path.relative_to( DOCS_ROOT )
    except ValueError:
        return path


TASK_OPEN_RE = re.compile(
    r'<li class="construction-task"([^>]*)>'
)
ATTR_RE = re.compile( r'(data-task-id|data-task-label|data-task-status)="([^"]*)"' )
LESSON_DIV_RE = re.compile(
    r'<div class="construction-task-lesson"([^>]*)>'
)
LESSON_ATTR_RE = re.compile( r'(data-lesson-src|data-lesson-title)="([^"]*)"' )
NEXT_TASK_OR_CHILDREN_RE = re.compile(
    r'<li class="construction-task"|<ol class="construction-task-children">'
)
LESSON_BADGE_RE = re.compile( r'class="construction-lesson-badge(?:\s+status-([a-z]+))?"' )
QUEUE_TABLE_MARKER = "Object to work on"
QUEUE_ROW_RE = re.compile(
    r'<tr>\s*<td>\d+</td>\s*<td><a href="([^"]+)"[^>]*>.*?</a>.*?</td>\s*'
    r'<td><span class="construction-status-chip(?:\s+status-([a-z]+))?">',
    re.DOTALL,
)

# task_id -> the data-lesson-src a task's <div class="construction-task-lesson">
# MUST carry, keyed per plan file. Most lessons are legitimately embedded
# inline with no leaf file of their own (or, like Event Contracts and
# Publish Barrel, embedded inline *and* separately mirrored in a standalone
# leaf page by hand -- that duplication is accepted, see building-the-spa
# SKILL.md's "Construction Status task trees" section). Only add an entry
# here for a task that CLAUDE.md documents as having promoted its leaf file
# to be the sole source of truth -- this is what would have caught the plan
# silently reverting to a stale 245-line inline copy instead of the
# data-lesson-src reference it was supposed to use. Keep entries here in
# sync with CLAUDE.md's "Interface File Construction Status source" note.
REQUIRED_LESSON_SRC = {
    "voice_communication/conversation_agent/basic_agent_construction_status.html": {
        "stage-1a": "voice_communication/conversation_agent/basic_agent_construction_status/declare_the_plug_in_point/interface_file/index.html",
        "stage-1b": "voice_communication/conversation_agent/basic_agent_construction_status/declare_the_plug_in_point/event_contracts/index.html",
    },
    "spa_browser_tests/characterization_suite/basic_agent_construction_status.html": {
        "stage-1a": "spa_browser_tests/characterization_suite/basic_agent_construction_status/declare_the_test_seams/platform_profile/index.html",
        "stage-4a": "spa_browser_tests/characterization_suite/basic_agent_construction_status/characterize_the_legacy_spa/characterization_suite/index.html",
    },
}


@dataclass
class Finding:
    kind: str
    plan_file: Path
    detail: str


def _find_plan_files( root: Path ) -> list[ Path ]:
    return sorted( root.rglob( "basic_agent_construction_status.html" ) )


def _parse_tasks( text: str ) -> list[ dict ]:
    """One dict per <li class="construction-task"> with its attributes and,
    if present, the data-lesson-src/data-lesson-title of the lesson div that
    immediately follows it (before the next sibling task or child list).
    """
    tasks = []
    for match in TASK_OPEN_RE.finditer( text ):
        attrs = dict( ATTR_RE.findall( match.group( 1 ) ) )
        if "data-task-id" not in attrs:
            continue

        window_end_match = NEXT_TASK_OR_CHILDREN_RE.search( text, match.end() )
        window_end = window_end_match.start() if window_end_match else len( text )
        window = text[ match.end() : window_end ]

        lesson_div = LESSON_DIV_RE.search( window )
        lesson_attrs = dict( LESSON_ATTR_RE.findall( lesson_div.group( 1 ) ) ) if lesson_div else {}

        tasks.append( {
            "task_id": attrs.get( "data-task-id" ),
            "label": attrs.get( "data-task-label", attrs.get( "data-task-id" ) ),
            "status": attrs.get( "data-task-status" ),
            "lesson_src": lesson_attrs.get( "data-lesson-src" ),
        } )
    return tasks


def _lesson_badge_status( lesson_path: Path ) -> str | None:
    if not lesson_path.exists():
        return None
    match = LESSON_BADGE_RE.search( lesson_path.read_text( encoding="utf-8" ) )
    if not match:
        return None
    return match.group( 1 ) or "not-started"


def check_task_lesson_agreement( plan_file: Path, text: str ) -> list[ Finding ]:
    findings = []
    for task in _parse_tasks( text ):
        if not task[ "lesson_src" ]:
            continue

        lesson_path = ( DOCS_ROOT / task[ "lesson_src" ] ).resolve()
        if not lesson_path.exists():
            findings.append( Finding(
                "BROKEN_LESSON_LINK", plan_file,
                f'task "{task[ "label" ]}" ({task[ "task_id" ]}) data-lesson-src '
                f'points at a missing file: {task[ "lesson_src" ]}',
            ) )
            continue

        badge_status = _lesson_badge_status( lesson_path )
        if badge_status is None:
            continue
        if badge_status != task[ "status" ]:
            findings.append( Finding(
                "STATUS_MISMATCH", plan_file,
                f'task "{task[ "label" ]}" ({task[ "task_id" ]}) is data-task-status='
                f'"{task[ "status" ]}" but its lesson badge says "{badge_status}" '
                f'({_display( lesson_path )})',
            ) )
    return findings


def check_queue_table( plan_file: Path, text: str ) -> list[ Finding ]:
    findings = []
    if QUEUE_TABLE_MARKER not in text:
        return findings

    for href, chip_suffix in QUEUE_ROW_RE.findall( text ):
        chip_status = chip_suffix or "not-started"
        target = ( plan_file.parent / href.split( "#" )[ 0 ] ).resolve()
        if not target.exists():
            findings.append( Finding(
                "BROKEN_QUEUE_LINK", plan_file,
                f'queue row links to a missing file: {href}',
            ) )
            continue

        badge_status = _lesson_badge_status( target )
        # A queue chip with no status- suffix reads "Not started"; the matching
        # badge suffix on a task page is spelled "planned", not "not-started".
        normalized_badge = "not-started" if badge_status == "planned" else badge_status
        if normalized_badge is not None and normalized_badge != chip_status:
            findings.append( Finding(
                "STATUS_MISMATCH", plan_file,
                f'queue row for {href} is marked "{chip_status}" but the target '
                f'page\'s own badge says "{badge_status}"',
            ) )
    return findings


def check_duplicate_queue_tables( plan_file: Path ) -> list[ Finding ]:
    """A lesson under this plan's own subtree should never carry a second
    full copy of the queue table -- only a short pointer back to the plan.
    """
    lesson_root = plan_file.parent / plan_file.stem
    if not lesson_root.is_dir():
        return []

    findings = []
    for candidate in lesson_root.rglob( "index.html" ):
        if QUEUE_TABLE_MARKER in candidate.read_text( encoding="utf-8" ):
            findings.append( Finding(
                "DUPLICATE_QUEUE_TABLE", plan_file,
                f'{_display( candidate )} contains its own copy of the '
                f'"{QUEUE_TABLE_MARKER}" queue table -- it should link to the plan\'s '
                f'queue section instead',
            ) )
    return findings


def check_required_lesson_src( plan_file: Path, text: str ) -> list[ Finding ]:
    """A task in REQUIRED_LESSON_SRC has promoted its leaf file to be the
    sole source of truth; if the plan's copy of that task doesn't carry
    the matching data-lesson-src, it has regressed to (or never left)
    embedding its own inline copy -- see the constant's docstring above.
    """
    required = REQUIRED_LESSON_SRC.get( _display( plan_file ).as_posix() )
    if not required:
        return []

    tasks_by_id = { task[ "task_id" ]: task for task in _parse_tasks( text ) }
    findings = []
    for task_id, expected_src in required.items():
        task = tasks_by_id.get( task_id )
        if task is None:
            findings.append( Finding(
                "MISSING_REQUIRED_LESSON_SRC", plan_file,
                f'task {task_id!r} listed in REQUIRED_LESSON_SRC no longer exists in this plan '
                f'-- update the constant if the task was renamed or removed',
            ) )
            continue
        if task[ "lesson_src" ] != expected_src:
            findings.append( Finding(
                "MISSING_REQUIRED_LESSON_SRC", plan_file,
                f'task "{task[ "label" ]}" ({task_id}) must use data-lesson-src="{expected_src}" '
                f'per CLAUDE.md, but has {task[ "lesson_src" ]!r} -- the plan may be embedding a '
                f'stale duplicate instead of referencing the leaf file',
            ) )
    return findings


def check_plan_file( plan_file: Path ) -> list[ Finding ]:
    text = plan_file.read_text( encoding="utf-8" )
    return [
        *check_task_lesson_agreement( plan_file, text ),
        *check_queue_table( plan_file, text ),
        *check_duplicate_queue_tables( plan_file ),
        *check_required_lesson_src( plan_file, text ),
    ]


def main( argv: list[ str ] ) -> int:
    if argv:
        plan_files = [ Path( argv[ 0 ] ).resolve() ]
    else:
        plan_files = _find_plan_files( DOCS_ROOT )

    findings = [ f for plan_file in plan_files for f in check_plan_file( plan_file ) ]

    if not findings:
        print( f"No inconsistencies found across {len( plan_files )} plan file(s)." )
        return 0

    for finding in findings:
        print( f'[{finding.kind}] {_display( finding.plan_file )}: {finding.detail}' )
    print( f"\n{len( findings )} finding(s) across {len( plan_files )} plan file(s)." )
    return 1


if __name__ == "__main__":
    sys.exit( main( sys.argv[ 1: ] ) )
