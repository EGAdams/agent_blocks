"""What an inspection produces. Pydantic, because it crosses the ADW -> worker -> gateway boundary as JSON."""
from __future__ import annotations

from pydantic import BaseModel, Field


class PageImage(BaseModel):
    """One rendered page. `path` is on this machine; the worker uploads it and the gateway serves it."""
    page: int
    rendition: str                      # "preview" (small, fast) or "full" (legible detail)
    path: str
    mime: str                           # image/jpeg, or image/png for bilevel line art
    width: int
    height: int
    bytes: int
    sha256: str


class Inspection(BaseModel):
    """Everything a remote reader gets of one file. The original is never modified."""
    kind: str                           # image | pdf | text | document | spreadsheet | archive | binary
    mime: str
    page_count: int = 0                 # pages in the file, which may exceed the pages rendered
    pages: list[ PageImage ] = Field( default_factory=list )
    text: str = ""
    text_truncated: bool = False
    note: str = ""                      # what could not be done, said plainly (no viewer, too many pages ...)
