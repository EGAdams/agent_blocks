"""DirectoryToolCatalog -- what tools an agent folder declares.

Single responsibility: enumerate `tools/<name>/` and report each one's
declaration. It does **not** load, import or execute anything: a tool folder
is a statement of intent, and turning `tools/bank_login/` into a working
capability is a separate job for a separate agent (see agents/README.md).

So a folder with no `tool.json` reports as a stub named after the directory,
which is exactly what `toyota/tools/bank_login` and `toyota/tools/web_search`
currently are.
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from .models import ToolDeclaration


class InvalidToolDeclaration( RuntimeError ):
    """`tool.json` exists but does not describe a ToolDeclaration."""


class DirectoryToolCatalog:
    """IToolCatalog over one agent's tools directory."""

    def __init__( self, tools_dir: Path, declaration_filename: str = "tool.json" ) -> None:
        self._tools_dir = tools_dir
        self._declaration_filename = declaration_filename

    def declarations( self ) -> tuple[ ToolDeclaration, ... ]:
        if not self._tools_dir.is_dir():
            return ()
        found: list[ ToolDeclaration ] = []
        for child in sorted( self._tools_dir.iterdir()):
            if not child.is_dir() or child.name.startswith( ( ".", "__" )):
                continue
            found.append( self._declaration( child ))
        return tuple( found )

    def _declaration( self, tool_root: Path ) -> ToolDeclaration:
        path = tool_root / self._declaration_filename
        if not path.is_file():
            return ToolDeclaration( name=tool_root.name )
        try:
            payload = json.loads( path.read_text( encoding="utf-8" ))
        except json.JSONDecodeError as error:
            raise InvalidToolDeclaration( f"{ path }: { error }" ) from error
        payload.setdefault( "name", tool_root.name )
        try:
            return ToolDeclaration.model_validate( payload )
        except ValidationError as error:
            raise InvalidToolDeclaration( f"{ path }: { error }" ) from error
