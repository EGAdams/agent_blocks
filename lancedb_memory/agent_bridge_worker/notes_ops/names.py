"""Note file names. Storage is flat -- ``<date>_<slug>.md`` -- and a category is only the slug's leading ``NN-``
token (``2026-11-05_04-deed.md`` is category 04), exactly as the dashboard's Notes list groups them. So creating
in a category and moving between categories are both just choosing a file name; this module is the one place
that does it, with the dashboard's own slug rule.
"""
from __future__ import annotations

import re

from .base import NotesRefusal

CATEGORIES = range( 1, 11 )
MAX_STEM   = 60
_UNSAFE    = re.compile( r"[^a-z0-9]+" )
_NOTE      = re.compile( r"^(?P<date>\d{4}-\d{2}-\d{2})_(?P<stem>[a-z0-9][a-z0-9-]*)\.md$" )
_CATEGORY  = re.compile( r"^(?P<n>\d{2})-" )
_TRASHED   = re.compile( r"^\d{14}_(?:\d+_)?" )


def slugify( name: str ) -> str:
    stem = _UNSAFE.sub( "-", str( name or "" ).strip().lower()).strip( "-" )
    return stem[ :MAX_STEM ].strip( "-" )


def create_hint( name: str, category: int | None ) -> str:
    """The name hint for a new note: the dashboard dates it, slugs it and makes it unique."""
    stem = slugify( name )
    if not stem:
        raise NotesRefusal( "a note needs a name with letters or digits in it" )
    if category is not None and category not in CATEGORIES:
        raise NotesRefusal( "category must be 1-10" )
    return f"{ category:02d}-{ stem }" if category else stem


def renamed( filename: str, new_name: str | None, category: int | None ) -> str:
    """The file name after a rename and/or a category move (category 0 = none). Date prefix kept."""
    match = _NOTE.match( filename )
    if not match:
        raise NotesRefusal( f"{ filename } is not a dated <date>_<name>.md note, so it cannot be renamed or moved" )
    stem  = match.group( "stem" )
    found = _CATEGORY.match( stem )
    current = int( found.group( "n" )) if found and int( found.group( "n" )) in CATEGORIES else None
    base  = stem[ found.end(): ] if current is not None else stem
    if new_name is not None:
        base = slugify( new_name )
        if not base:
            raise NotesRefusal( "a note needs a name with letters or digits in it" )
    if category is not None and category != 0 and category not in CATEGORIES:
        raise NotesRefusal( "category must be 1-10" )
    target = current if category is None else ( None if category == 0 else category )
    new_stem = ( f"{ target:02d}-{ base }" if target is not None else base )[ :MAX_STEM ].strip( "-" )
    return f"{ match.group( 'date' )}_{ new_stem }.md"


def original_of( trash_id: str ) -> str:
    """The file name a trashed note had before it was trashed."""
    return _TRASHED.sub( "", trash_id, count=1 )
