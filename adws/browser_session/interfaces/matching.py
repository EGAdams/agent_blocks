"""Specifications and matchers: yes/no questions, each about one thing."""
from __future__ import annotations

from typing import Protocol

from .page import IPageElement


class ITextMatcher( Protocol ):
    """Does an element's visible text count as the text we want?"""

    @property
    def label( self ) -> str: ...
    def matches( self, text: str ) -> bool: ...


class IElementSpecification( Protocol ):
    """Specification pattern over elements: is this one usable?"""

    def is_satisfied_by( self, element: IPageElement ) -> bool: ...


class IUrlSpecification( Protocol ):
    """Specification pattern over URLs: is this tab the right site?"""

    @property
    def label( self ) -> str: ...
    def is_satisfied_by( self, url: str ) -> bool: ...
