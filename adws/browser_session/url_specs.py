"""IUrlSpecification: is this tab on the site we want?"""
from __future__ import annotations

from urllib.parse import urlparse

from .interfaces import IUrlSpecification


class HostSuffixSpecification:
    """Host is `suffix` or a subdomain of it, on a dot boundary:
    `secure.53.com` matches `53.com`; `not53.com` does not."""

    def __init__( self, suffix: str ):
        self._suffix = suffix.lower().lstrip( "." )

    @property
    def label( self ) -> str:
        return f"host:{ self._suffix }"

    def is_satisfied_by( self, url: str ) -> bool:
        host = ( urlparse( url ).hostname or "" ).lower()
        return host == self._suffix or host.endswith( "." + self._suffix )


class HttpsSpecification:
    """Only an https page -- a sign-in button on plain http is not pressed."""

    @property
    def label( self ) -> str:
        return "https"

    def is_satisfied_by( self, url: str ) -> bool:
        return urlparse( url ).scheme == "https"


class AllOfUrlSpecification:
    """Composite: satisfied when every part is."""

    def __init__( self, parts: list[ IUrlSpecification ]):
        self._parts = parts

    @property
    def label( self ) -> str:
        return " & ".join( p.label for p in self._parts )

    def is_satisfied_by( self, url: str ) -> bool:
        return all( p.is_satisfied_by( url ) for p in self._parts )
