"""Choosing which open tab to work in."""
from __future__ import annotations

from typing import Protocol

from ..models import TabChoice, TabInfo


class ITabSelector( Protocol ):
    def choose( self, tabs: list[ TabInfo ]) -> TabChoice: ...
