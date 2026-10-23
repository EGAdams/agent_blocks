"""DirectoryDocumentationSet -- which documentation an agent carries.

**Deliberately a stub.** It reports which directories under
`documentation/` exist and which are still empty, and nothing more. Filling
`documentation/mermaid diagrams/{flowchart,sequence,class}` and
`documentation/html_manual/{toc,public_links}` is a generation job -- one
pass over an agent's prompts, tools and model config to emit diagrams and an
HTML manual -- and it belongs to a documentation agent, not to the loader
that has to stay fast enough to run on every catalog build.

What this class *is* good for right now: `cli.py validate` can tell you at a
glance which agents have documentation and which are waiting for that agent
to be written.
"""
from __future__ import annotations

from pathlib import Path

from .models import DocumentationIndex


class DirectoryDocumentationSet:
    """IDocumentationSet over one agent's documentation directory."""

    def __init__( self, documentation_dir: Path ) -> None:
        self._documentation_dir = documentation_dir

    def index( self ) -> DocumentationIndex:
        if not self._documentation_dir.is_dir():
            return DocumentationIndex()
        present: list[ str ] = []
        empty: list[ str ] = []
        for path in sorted( self._documentation_dir.rglob( "*" )):
            if not path.is_dir():
                continue
            relative = str( path.relative_to( self._documentation_dir ))
            has_files = any( child.is_file() for child in path.iterdir())
            ( present if has_files else empty ).append( relative )
        return DocumentationIndex( present=present, empty=empty )
