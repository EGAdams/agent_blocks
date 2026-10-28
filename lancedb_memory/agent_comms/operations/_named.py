"""A mixin for the three descriptive properties every operation has."""
from __future__ import annotations


class Named:
    NAME        = ""
    DESCRIPTION = ""
    PERFORMER   = "python"

    @property
    def name( self ) -> str:
        return self.NAME

    @property
    def description( self ) -> str:
        return self.DESCRIPTION

    @property
    def performer( self ) -> str:
        return self.PERFORMER
