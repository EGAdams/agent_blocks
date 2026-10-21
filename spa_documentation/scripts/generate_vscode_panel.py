#!/usr/bin/env python3
"""Generate a `.vscode-editor` source panel for a Construction Status lesson.

The lesson pages under `voice_communication/conversation_agent/` show a
completed item's real source inside VS Code chrome -- filename tab, gutter
line numbers, status bar -- because the frame is what makes a reader trust
it is the file on disk rather than a paraphrase. The markup for that frame
is fiddly (a two-column grid per line, real line numbers preserved across
excerpt gaps, per-language tab badges), so generate it rather than hand-
writing it.

Run with the repo's .venv, which has Pygments:

    .venv/bin/python scripts/generate_vscode_panel.py \\
        ../claude_agent_adapter/models.py \\
        --tab claude_agent_adapter/models.py \\
        --lines 7-8 --lines 15-25 --caret 15

Paths are resolved against the agent_blocks workspace root (the parent of
this repo), so pass them the way they read in the lesson prose. Print goes
to stdout; paste the single line it emits into the lesson's HTML.

--lines may be repeated. Each extra range renders a `vscode-code-elision`
row -- a `···` gutter and a `⋮` body -- in place of the skipped lines, which
is what keeps the surviving line numbers checkable against the real file.
Prefer real ranges over a whole file when the item owns only part of one:
the Event Contracts lesson shows 5 KB of `contracts.ts` instead of 19 KB.
"""
import argparse
import html
import sys
from pathlib import Path

from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_for_filename

DOCS_ROOT = Path( __file__ ).resolve().parent.parent
REPO_ROOT = DOCS_ROOT.parent

# The tab badge is per-language and styled in styles.css under
# `.construction-lesson`; there is no generic fallback badge there, so a new
# language needs a CSS rule before it needs a row here.
BADGES = {
    ".py": ( "vscode-python-badge", "PY" ),
    ".ts": ( "vscode-ts-badge", "TS" ),
    ".tsx": ( "vscode-ts-badge", "TS" ),
}
INDENT_WIDTH = { ".py": 4 }


def parse_range( text: str ) -> tuple[ int, int ]:
    start, _, end = text.partition( "-" )
    return ( int( start ), int( end or start ) )


def build_panel(
    source: Path,
    tab_label: str,
    ranges: list[ tuple[ int, int ] ],
    caret: int,
) -> str:
    suffix = source.suffix
    badge_class, badge_text = BADGES.get( suffix, ( "vscode-text-badge", "TXT" ) )
    language = get_lexer_for_filename( source.name ).name
    spaces = INDENT_WIDTH.get( suffix, 2 )

    # nowrap keeps Pygments' token spans without its own <div><pre> wrapper,
    # so each line can go into this file's own grid row instead.
    lines = highlight(
        source.read_text(),
        get_lexer_for_filename( source.name ),
        HtmlFormatter( nowrap=True ),
    ).split( "\n" )

    rows: list[ str ] = []
    for index, ( start, end ) in enumerate( ranges ):
        if index > 0:
            rows.append(
                '<span class="vscode-code-line vscode-code-elision">'
                '<span aria-hidden="true" class="vscode-line-number">···</span>'
                '<span class="vscode-line-text">⋮</span></span>'
            )
        for number in range( start, end + 1 ):
            if number > len( lines ):
                raise SystemExit( f"{source} has {len( lines )} lines; asked for {number}" )
            # A blank line still needs a body, or the grid row collapses.
            text = lines[ number - 1 ] or " "
            rows.append(
                '<span class="vscode-code-line">'
                f'<span aria-hidden="true" class="vscode-line-number">{number}</span>'
                f'<span class="vscode-line-text">{text}</span></span>'
            )

    return (
        f'<div class="vscode-editor" data-language="{language.lower()}">'
        '<div class="vscode-titlebar"><div class="vscode-app">'
        '<span class="vscode-logo">&lt;&gt;</span> Visual Studio Code</div>'
        '<div aria-hidden="true" class="vscode-window-controls">—   □   ×</div></div>'
        '<div class="vscode-tabs"><div class="vscode-tab">'
        f'<span class="{badge_class}">{badge_text}</span>'
        f'<span>{html.escape( tab_label )}</span>'
        '</div></div>'
        '<pre class="vscode-code"><code>' + "".join( rows ) + '</code></pre>'
        '<div class="vscode-statusbar">'
        '<div class="vscode-status-left">master   ✓ 0   ⚠ 0</div>'
        f'<div class="vscode-status-right">Ln {caret}, Col 1   Spaces: {spaces}'
        f'   UTF-8   LF   {language}</div></div>'
        '</div>'
    )


def main() -> int:
    parser = argparse.ArgumentParser( description=__doc__ )
    parser.add_argument( "source", help="source file, relative to the agent_blocks root" )
    parser.add_argument( "--tab", help="tab label (defaults to the source path as given)" )
    parser.add_argument(
        "--lines",
        action="append",
        default=[],
        metavar="START-END",
        help="line range to include; repeat for an excerpt with elision rows",
    )
    parser.add_argument( "--caret", type=int, help="status-bar line number (defaults to the first line shown)" )
    args = parser.parse_args()

    source = ( REPO_ROOT / args.source ).resolve()
    if not source.is_file():
        raise SystemExit( f"no such source file: {source}" )

    total = len( source.read_text().splitlines() )
    ranges = [ parse_range( item ) for item in args.lines ] or [ ( 1, total ) ]
    print( build_panel(
        source,
        args.tab or args.source,
        ranges,
        args.caret or ranges[ 0 ][ 0 ],
    ) )
    return 0


if __name__ == "__main__":
    sys.exit( main() )
