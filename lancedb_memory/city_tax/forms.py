"""The City of Grand Rapids' blank forms, read off its own page -- never a guessed URL.

`CityFormsPage` is the IOfficialFormProvider: it fetches the Individual
Taxpayers page and lists every PDF link with the year, residency, kind and
"fillable" read from the link's own words. The city only keeps the current
year's packet up; for an earlier year's instructions, `WaybackForms` asks the
Internet Archive for its copy of the URL the city published then (the URL is
read from the 2024 return's saved extract, not assumed), and marks the result
`archived=True` so no report presents it as a live city link.

A download never overwrites: the same bytes are kept, different bytes get a
new name, and every file is recorded with its URL, sha256 and retrieval time.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from .models import FormKind, OfficialForm
from .store import private_dir

CITY_PAGE = "https://www.grandrapidsmi.gov/departments/fiscal-services/income-tax/individual-taxpayers/"
_AGENT = "Mozilla/5.0 (Liz city-tax ADW; read-only)"
_LINK = re.compile( r'<a[^>]+href="([^"]+\.pdf[^"]*)"[^>]*>(.*?)</a>', re.I | re.S )


def fetch( url: str, timeout: float = 60.0 ) -> bytes:
    request = urllib.request.Request( url, headers={ "User-Agent": _AGENT })
    with urllib.request.urlopen( request, timeout=timeout ) as response:   # noqa: S310 -- https/http only
        return response.read()


def _slug( text: str ) -> str:
    return re.sub( r"[^a-z0-9]+", "-", text.lower()).strip( "-" )


def classify_link( label: str, url: str, listed_on: str ) -> OfficialForm | None:
    """One link's form, from its words and file name. None when it isn't an individual form."""
    words = f"{ label } { urllib.parse.unquote( url )}".lower()
    year_match = re.search( r"\b(20\d\d)\b", label ) or re.search( r"(20\d\d)", url )
    prior = "prior year" in words or "prior-" in words
    if "spanish" in words or "amended" in words or "power of attorney" in words or "poa" in words:
        return None
    if "extension" in words or "4868" in words:
        kind = FormKind.EXTENSION
    elif "estimate" in words or "1040es" in words or "2210" in words or "eft" in words:
        kind = FormKind.ESTIMATE
    elif "printable" in words:
        kind = FormKind.INSTRUCTIONS
    elif "return" in words or "common-form" in words:
        kind = FormKind.RETURN
    else:
        kind = FormKind.OTHER
    if "non resident" in words or "non-resident" in words or "nonres" in words:
        residency = "non-resident"
    elif "part year" in words or "part-year" in words or "-part-" in words:
        residency = "part-year"
    elif "resident" in words or "-res-" in words:
        residency = "resident"
    else:
        residency = "any"
    tax_year = int( year_match.group( 1 )) if year_match and not prior else 0
    fillable = "fillable" in words or ( prior and kind is FormKind.RETURN and "printable" not in words )
    title = re.sub( r"\s+", " ", label ).strip()
    if prior:
        title = f"{ title } (prior-year common form; the year is chosen on the form)"
    key = _slug( f"{ tax_year or 'prior' }-{ residency }-{ kind.value }-{ 'fillable' if fillable else 'printable' }" )
    return OfficialForm( key=key, title=title, tax_year=tax_year, residency=residency, kind=kind,
                         fillable=fillable, url=url, listed_on=listed_on )


def parse_forms_page( page: str, base: str = CITY_PAGE ) -> list[ OfficialForm ]:
    forms: dict[ str, OfficialForm ] = {}
    for href, inner in _LINK.findall( page ):
        label = html.unescape( re.sub( r"<[^>]+>", " ", inner ))
        url = urllib.parse.urljoin( base, html.unescape( href ))
        form = classify_link( label, url, base )
        if form is not None and form.key not in forms:
            forms[ form.key ] = form
    return list( forms.values())


def _record_download( form: OfficialForm, data: bytes, folder: Path ) -> OfficialForm:
    private_dir( folder )
    digest = hashlib.sha256( data ).hexdigest()
    name = Path( urllib.parse.urlparse( form.url ).path ).name or f"{ form.key }.pdf"
    target = folder / name
    if target.exists() and hashlib.sha256( target.read_bytes()).hexdigest() != digest:
        target = folder / f"{ target.stem }.{ digest[ :8 ] }{ target.suffix }"   # never overwrite
    if not target.exists():
        target.write_bytes( data )
        target.chmod( 0o600 )
    return form.model_copy( update={ "local_path": str( target ), "sha256": digest,
                                     "retrieved_at": datetime.now().astimezone()})


class CityFormsPage:
    """IOfficialFormProvider over the city's Individual Taxpayers page."""

    def __init__( self, page_url: str = CITY_PAGE, fetcher=fetch ) -> None:
        self._url = page_url
        self._fetch = fetcher

    def list_forms( self ) -> list[ OfficialForm ]:
        return parse_forms_page( self._fetch( self._url ).decode( "utf-8", "replace" ), self._url )

    def download( self, form: OfficialForm, folder: Path ) -> OfficialForm:
        data = self._fetch( form.url )
        if not data.startswith( b"%PDF" ):
            raise ValueError( f"{ form.url } did not return a PDF." )
        return _record_download( form, data, folder )


class WaybackForms:
    """An earlier year's packet, from the Internet Archive's copy of the city's own URL."""

    def __init__( self, fetcher=fetch ) -> None:
        self._fetch = fetcher

    def find( self, original_url: str, title: str, tax_year: int, kind: FormKind,
              residency: str = "resident" ) -> OfficialForm | None:
        query = "https://archive.org/wayback/available?url=" + urllib.parse.quote( original_url, safe="" )
        try:
            closest = json.loads( self._fetch( query ))[ "archived_snapshots" ][ "closest" ]
        except ( OSError, ValueError, KeyError, TypeError ):
            return None
        if not closest.get( "available" ):
            return None
        # `id_` asks for the bytes as archived, without the archive's page frame.
        raw = re.sub( r"/web/(\d+)/", r"/web/\1id_/", closest[ "url" ])
        return OfficialForm( key=_slug( f"{ tax_year }-{ residency }-{ kind.value }-archived" ), title=title,
                             tax_year=tax_year, residency=residency, kind=kind, fillable=False, url=raw,
                             listed_on=original_url, archived=True )

    def download( self, form: OfficialForm, folder: Path ) -> OfficialForm:
        data = self._fetch( form.url )
        if not data.startswith( b"%PDF" ):
            raise ValueError( f"The archive's copy of { form.listed_on } is not a PDF." )
        return _record_download( form, data, folder )
