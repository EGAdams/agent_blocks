"""Which ADWs on disk are hooked into the object trace, read from their source -- never run.

An ADW is hooked one of two ways:

    self-traced       it wraps itself in `AdwRunTrace` (adw_bridge_worker, adw_notes_render_check)
    shared executor   it reaches `sssf_roles` -- directly or through a local harness such as
                      browser_workflow_adw, post_office_adw or liz_adw -- whose phase executor
                      is the traced one from `object_trace.sssf_hooks`

An ADW written straight against `adw_modules` (`with run.phase(...)`, the
classic plan/build/scout set) cannot be hooked from here -- `adw_modules` is
shared machinery this workspace does not edit -- so its sessions and phases
are read, read-only, from sssf.db, and it is listed as `sssf sessions only`.
Anything else is listed as `not hooked`, so the gap is visible rather than assumed away.
This is a static reading of imports: a hooked ADW that has never run has no
objects yet, and the inventory says so separately.
"""
from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel

SELF_TRACED = "self-traced (AdwRunTrace)"
SHARED      = "shared executor (sssf_roles)"
SSSF_ONLY   = "sssf sessions only (adw_modules; read through the SSSF reader)"
NOT_HOOKED  = "not hooked"

_IMPORT = re.compile( r"^\s*(?:from\s+([A-Za-z_][\w]*)[\w.]*\s+import|import\s+([A-Za-z_][\w]*))", re.M )


class AdwCoverage( BaseModel ):
    adw: str
    hook: str
    via: str | None = None


def _imports( text: str ) -> set[ str ]:
    return { a or b for a, b in _IMPORT.findall( text )}


def _local_sources( adws: Path ) -> dict[ str, str ]:
    """Top-level local module name -> its source (a package's is all its files joined)."""
    out: dict[ str, str ] = {}
    for f in adws.glob( "*.py" ):
        out[ f.stem ] = _read( f )
    for d in adws.iterdir():
        if d.is_dir() and ( d / "__init__.py" ).is_file() and d.name != "adw_modules":
            out[ d.name ] = "\n".join( _read( f ) for f in sorted( d.rglob( "*.py" )) if "tests" not in f.parts )
    return out


def _read( f: Path ) -> str:
    try:
        return f.read_text( encoding="utf-8", errors="replace" )
    except OSError:
        return ""


def adw_coverage( adws: Path ) -> list[ AdwCoverage ]:
    adws    = Path( adws )
    if not adws.is_dir():
        return []
    sources = _local_sources( adws )
    deps    = { name: _imports( text ) & set( sources ) for name, text in sources.items()}
    reaches: dict[ str, str ] = { "sssf_roles": "sssf_roles" }
    changed = True
    while changed:                            # which local modules lead to sssf_roles, and through what
        changed = False
        for name, uses in deps.items():
            if name in reaches:
                continue
            hit = next(( u for u in sorted( uses ) if u in reaches ), None )
            if hit is not None:
                reaches[ name ] = hit
                changed = True
    rows = []
    for f in sorted( adws.glob( "adw_*.py" )):
        text = sources.get( f.stem, "" )
        if "AdwRunTrace(" in text:
            rows.append( AdwCoverage( adw=f.stem, hook=SELF_TRACED ))
        elif f.stem in reaches:
            rows.append( AdwCoverage( adw=f.stem, hook=SHARED, via=reaches[ f.stem ]))
        elif "adw_modules" in _imports( text ):
            rows.append( AdwCoverage( adw=f.stem, hook=SSSF_ONLY, via="adw_modules" ))
        else:
            rows.append( AdwCoverage( adw=f.stem, hook=NOT_HOOKED ))
    return rows
