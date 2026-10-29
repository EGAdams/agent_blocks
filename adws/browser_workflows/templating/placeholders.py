"""The `{placeholder}` syntax, in one place. Lower-case names only, so a
brace that happens to be in a page's text is left alone."""
from __future__ import annotations

import re

PLACEHOLDER = re.compile( r"\{([a-z][a-z0-9_]*)\}" )


def names_in( text: str | None ) -> list[ str ]:
    return PLACEHOLDER.findall( text or "" )


def fill( text: str | None, values: dict[ str, str ]) -> str | None:
    if text is None:
        return None
    return PLACEHOLDER.sub( lambda m: values.get( m.group( 1 ), m.group( 0 )), text )
