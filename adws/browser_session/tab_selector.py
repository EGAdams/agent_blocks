"""ITabSelector: the first open tab an IUrlSpecification accepts."""
from __future__ import annotations

from .interfaces import IUrlSpecification
from .models import TabChoice, TabInfo


class SpecificationTabSelector:

    def __init__( self, spec: IUrlSpecification ):
        self._spec = spec

    def choose( self, tabs: list[ TabInfo ]) -> TabChoice:
        seen = [ t.url for t in tabs ]
        for tab in tabs:
            if self._spec.is_satisfied_by( tab.url ):
                return TabChoice( found=True, handle=tab.handle, url=tab.url, seen=seen )
        return TabChoice( found=False, seen=seen )
