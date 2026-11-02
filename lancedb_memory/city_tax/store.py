"""Liz's private folder, her workpapers, and money rounding.

Everything lives under `~/liz_city_tax` (override: LIZ_CITY_TAX_HOME), mode
0700 / files 0600: it holds the family's tax figures, and it is outside every
web root. Only finished review pages are copied to ADW Generated, and they
never carry an SSN, a bank number or a street address.
"""
from __future__ import annotations

import json
import os
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from pydantic import BaseModel


def liz_home() -> Path:
    return Path( os.environ.get( "LIZ_CITY_TAX_HOME", str( Path.home() / "liz_city_tax" )))


def private_dir( path: Path ) -> Path:
    """Make `path`, and 0700 it and every folder between it and Liz's home."""
    path.mkdir( parents=True, exist_ok=True )
    home = liz_home().resolve()
    node = path.resolve()
    while True:
        os.chmod( node, 0o700 )
        if node == home or home not in node.parents:
            break
        node = node.parent
    return path


def write_private( path: Path, text: str ) -> Path:
    """Write by temp-and-rename, 0600, so a reader never sees half a file."""
    private_dir( path.parent )
    tmp = path.with_name( path.name + ".tmp" )
    fd = os.open( tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 )
    with os.fdopen( fd, "w", encoding="utf-8" ) as handle:
        handle.write( text )
    tmp.replace( path )
    return path


def whole_dollars( amount: Decimal ) -> Decimal:
    """The city's rule: drop under $0.50, raise $0.50-$0.99 to the next dollar."""
    return amount.quantize( Decimal( "1" ), rounding=ROUND_HALF_UP )


def _jsonable( data: object ) -> object:
    if isinstance( data, BaseModel ):
        return data.model_dump( mode="json" )
    if isinstance( data, list ):
        return [ _jsonable( item ) for item in data ]
    if isinstance( data, dict ):
        return { key: _jsonable( value ) for key, value in data.items()}
    return data


class JsonWorkpapers:
    """`<home>/work/<name>.json` -- one per ADW output."""

    def __init__( self, home: Path | None = None ) -> None:
        self._dir = ( home or liz_home()) / "work"

    def path( self, name: str ) -> Path:
        return self._dir / f"{ name }.json"

    def save( self, name: str, data: object ) -> Path:
        return write_private( self.path( name ), json.dumps( _jsonable( data ), indent=2, default=str ) + "\n" )

    def load( self, name: str ) -> object | None:
        try:
            return json.loads( self.path( name ).read_text( encoding="utf-8" ))
        except ( OSError, ValueError ):
            return None


class InMemoryWorkpapers:
    def __init__( self ) -> None:
        self.saved: dict[ str, object ] = {}

    def path( self, name: str ) -> Path:
        return Path( f"/memory/{ name }.json" )

    def save( self, name: str, data: object ) -> Path:
        self.saved[ name ] = json.loads( json.dumps( _jsonable( data ), default=str ))
        return self.path( name )

    def load( self, name: str ) -> object | None:
        return self.saved.get( name )
