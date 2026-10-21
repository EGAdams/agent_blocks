#!/usr/bin/env python3
# unset ANTHROPIC_API_KEY
# unset ANTHROPIC_BASE_URL
"""Claude Code SDK agent that keeps one spa_documentation item's detail
tabs (Source / Class Diagram / Sequence Diagram) in sync with its real
source file.

Usage:
    .venv/bin/python update_documentation_agent.py <item-path>

    (plain `python3` won't have claude_agent_sdk / pygments installed --
    see .venv, built the same way as lancedb_memory/.venv)

    item-path is a docs item path relative to spa_documentation/, e.g.:
        catherine_agent_sdk/basic_agent
        lancedb_memory/observers/cost_observer
        lancedb_memory/adapters/lancedb_store

Backs the "Update Documentation" tab on each item's detail page. Run this
script from the terminal to actually do the work; it writes its report into
<item>/basic_agent_update_documentation.html for the tab to display. The tab
itself only reads git status live (via server.py's /api/git-status route) to
enable/disable its button -- it never runs this script for you.

Uses the local Claude Code subscription auth (ANTHROPIC_API_KEY and
ANTHROPIC_BASE_URL must stay unset), the same as catherine_agent_sdk/basic_agent.py.
"""
import argparse
import asyncio
import re
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    query,
)

from doc_source_map import DOC_FILES, DOCS_ROOT, REPO_ROOT, resolve_source_path

# Reference pages the agent should read to copy structure/style from, since
# it has no shared template file to import -- see skills/building-the-spa.
REFERENCE_SOURCE_HTML = DOCS_ROOT / "catherine_agent_sdk/basic_agent/basic_agent.py.html"
REFERENCE_CLASS_DIAGRAM = DOCS_ROOT / "catherine_agent_sdk/basic_agent/mermaid_class.html"
REFERENCE_SEQUENCE_DIAGRAM = DOCS_ROOT / "catherine_agent_sdk/basic_agent/mermaid_sequence.html"

BLURRY_MERMAID_COMPOSITOR_HINT = re.compile(
    r"^[ \t]*will-change[ \t]*:[ \t]*transform[ \t]*;[ \t]*(?:\n|$)",
    re.MULTILINE,
)


def remove_blurry_mermaid_compositor_hint( doc_dir: Path ) -> list[ Path ]:
    """Keep generated Mermaid SVGs vector-sharp during CSS pan and zoom."""
    changed = []
    for key in ( "class_diagram", "sequence_diagram" ):
        path = doc_dir / DOC_FILES[ key ]
        if not path.is_file():
            continue
        html = path.read_text()
        cleaned = BLURRY_MERMAID_COMPOSITOR_HINT.sub( "", html )
        if cleaned == html:
            continue
        path.write_text( cleaned )
        changed.append( path )
    return changed


def build_task( item_path: str, doc_dir: Path, source_path: Path | None ) -> str:
    doc_paths = { key: doc_dir / name for key, name in DOC_FILES.items() }

    if source_path is None or not source_path.exists():
        return (
            f"There is no source file yet for docs item '{item_path}' "
            f"(looked for {source_path}). Write exactly one line to "
            f"{doc_paths[ 'report' ]} saying documentation cannot be generated "
            "because the source doesn't exist yet, and change nothing else. "
            "Do not touch any other file."
        )

    return f"""You are the documentation-maintenance agent for the agent_blocks
spa_documentation SPA. Your job is ONE item: '{item_path}'.

Real source file (read-only, never edit it):
    {source_path}

Its docs folder (the only place you may write):
    {doc_dir}
    overview             -> {doc_paths[ 'overview' ]}
    Source tab           -> {doc_paths[ 'source' ]}
    Class Diagram tab     -> {doc_paths[ 'class_diagram' ]}
    Sequence Diagram tab  -> {doc_paths[ 'sequence_diagram' ]}
    Construction Status   -> {doc_paths[ 'construction_status' ]}
    Update Documentation report (always write this last) -> {doc_paths[ 'report' ]}

Step 1 -- decide if anything is stale:
    Read the current source file and the current Source tab file. The Source
    tab is stale if it's empty, missing, or its highlighted code no longer
    matches the current source. The diagrams are stale if they're empty,
    missing, or no longer accurately reflect the current source's classes/
    methods/call flow (e.g. source changed but diagram wasn't regenerated).

Step 2 -- if NOTHING is stale:
    Write one short line to the report file stating documentation is already
    up to date, and do not modify any other file. Stop.

Step 3 -- if the Source tab is stale, regenerate {doc_paths[ 'source' ]}:
    Pre-render the Python source as VS Code Dark+-colored HTML using Pygments
    (there is no highlighter wired into the SPA at runtime) -- run it via
    Bash, e.g. a small python3 snippet using pygments.lexers.PythonLexer and
    pygments.formatters.HtmlFormatter with a VS Code Dark+ style class
    mapping. Follow the exact structure of the reference file at
    {REFERENCE_SOURCE_HTML} (read it first): a <style> block scoped to
    .vscode-pre plus the formatter's get_style_defs() output, then
    <pre class="vscode-pre"><code>...</code></pre> with the highlighted body.
    Turn any bare http(s) URL inside a '# comment' into a real
    <a target="_blank" rel="noopener noreferrer"> link colored `inherit` from
    the comment span, same as the reference. The heading itself is
    "<h1>{{item label}} Source</h1>" -- e.g. "<h1>Turn Source</h1>", not the
    bare "<h1>Source</h1>" the older reference file still has -- so the page
    reads clearly when several source tabs are open across browser history.
    Below that heading add one subtitle line using the existing .placeholder CSS class
    (no new CSS): <p class="placeholder">Lines: N&nbsp;&nbsp;Last modified:
    Month, day HH:MM:SS</p> -- get N via `wc -l` and the mtime via
    `stat -c '%y'` on the real source file, formatted as shown. This is a
    plain HTML fragment (loadFile() injects it directly into #content) --
    no <!DOCTYPE>, <html>, <head>, or <body> tags.

Step 4 -- if the diagrams are stale or missing, regenerate them:
    Read {REFERENCE_CLASS_DIAGRAM} and {REFERENCE_SEQUENCE_DIAGRAM} first and
    copy their structure exactly (the .mmd-wrap / .mmd-toolbar / #mmd-viewport
    / #mmd-stage pan-zoom pattern, mermaid.render() call, wheel-zoom and
    pointer-drag wiring) -- only replace the diagram definition text inside
    <pre class="mermaid" id="mmd-source"> with one that actually represents
    this source file's real classes/methods (class diagram) and its real
    call flow (sequence diagram). Give each mermaid.render() call a unique id
    (e.g. "mmd-svg-" + Date.now()). Never add `will-change: transform` to
    .mmd-stage: forcing the SVG into a persistent compositor layer makes its
    text blurry. Treat an otherwise-accurate diagram that still has that
    declaration as stale and remove only the declaration. Do not touch a
    diagram file that's already accurate and does not have that declaration.

Step 5 -- always finish by writing a short report to
    {doc_paths[ 'report' ]}: one or two sentences, plain HTML fragment
    (a <p> is enough), stating what you changed (Source / Class Diagram /
    Sequence Diagram / nothing) and today's date.

Constraints: only write inside {doc_dir}. Never modify the real source file.
Do not rewrite a file that's already correct just to have made an edit.
"""


async def run_agent( item_path: str ) -> None:
    doc_dir = DOCS_ROOT / item_path
    if not doc_dir.is_dir():
        print( f"No docs folder at {doc_dir} -- add the item to app.js and create its skeleton first." )
        return

    source_path = resolve_source_path( item_path )
    task = build_task( item_path, doc_dir, source_path )

    options = ClaudeAgentOptions(
        model="sonnet",
        cwd=str( REPO_ROOT ),
        allowed_tools=[ "Read", "Write", "Edit", "Bash", "Glob", "Grep" ],
        permission_mode="acceptEdits",
        max_turns=40,
    )

    print( f"Updating documentation for '{item_path}'...\n" )

    final_result = None

    async for message in query( prompt=task, options=options ):
        if isinstance( message, AssistantMessage ):
            for block in message.content:
                if isinstance( block, TextBlock ):
                    print( block.text, end="", flush=True )

        elif isinstance( message, ResultMessage ):
            final_result = message

    sanitized_diagrams = remove_blurry_mermaid_compositor_hint( doc_dir )
    for path in sanitized_diagrams:
        print( f"\nRemoved blur-causing Mermaid compositor hint from {path}" )

    print( "\n\nTask completed!" )

    if final_result is None:
        print( "No ResultMessage was returned." )
        return

    print( f"Status: { final_result.subtype }" )
    print( f"Is error: { final_result.is_error }" )

    if final_result.is_error:
        print( f"Error: { final_result.result }" )

    if final_result.total_cost_usd is not None:
        # Not "...:.6f }" -- a space inside a format spec is invalid syntax
        # (ValueError: Invalid format specifier), so the space moves before
        # the colon instead. Same latent bug exists uncorrected in the
        # reference file this was copied from, catherine_agent_sdk/basic_agent.py.
        print( f"Total Cost: ${ final_result.total_cost_usd :.6f}" )


if __name__ == "__main__":
    parser = argparse.ArgumentParser( description=__doc__ )
    parser.add_argument(
        "item_path",
        help="docs item path relative to spa_documentation/, e.g. lancedb_memory/observers/cost_observer",
    )
    args = parser.parse_args()

    asyncio.run( run_agent( args.item_path ))
