"""DownloadPublisher: hand a browser.download task's saved files to the gateway so ChatGPT can look at them.

The Browser Control ADW saves a file on the Windows desktop and renders its pages to images
(`adws/file_inspection`). That alone leaves ChatGPT with a path on a machine it cannot reach. This
uploads, for each saved file, the original (when it fits the gateway's blob limit) and every rendered
page as content-addressed blobs, then posts one lease-checked manifest. The gateway answers with a
`file_id` per file, which is what `browser_view_file` takes.

What goes back into the task's details is a summary -- the file id, its kind, page count and a note --
never the extracted text or the local rendition paths, which would only bloat the task record; the
text lives with the file at the gateway and is returned by `browser_view_file`.

A bytes-on-disk mismatch (the file changed after the ADW hashed it) is refused, not uploaded under the
old checksum, and a publish that fails leaves the download a success with `inspectable: false` and
the reason, because the file *was* saved -- it is the viewing that did not happen.

One download can be many files -- an archive's safely unpacked members follow it as files of their
own -- so two of the gateway's per-task limits are kept here rather than tripped there: at most
`MAX_FILES` are published (the rest stay on disk, named in the task as not published), and all their
text together fits `TEXT_BUDGET` bytes, shared out so a short file keeps all of its text and the long
ones split what is left. Each file says when its text was cut.

A publish is retried (`RETRY_WAITS`) when the gateway could not be reached or answered 5xx/429: the
live box's network drops for seconds at a time (`[Errno 101] Network is unreachable`, 2026-11-09), and
one blip used to strand a good download where ChatGPT could not see it. Retrying is safe because blobs
are content-addressed and the gateway's manifest write replaces the task's rows under deterministic
file ids. A refusal (any other 4xx -- a lost lease, a bad field) is never retried.
"""
from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from pathlib import Path

from .models import ClaimedTask
from .ports import GatewayError, IDownloadUploader

#: The gateway's blob ceiling (GatewayApp's MAX_BLOB). A larger original is described, not uploaded;
#: its rendered pages still are, so it can still be looked at.
MAX_BLOB = 7_340_032
#: Text sent per file. The gateway stores at most this much and says when it was cut.
MAX_TEXT = 100_000
PAGE_KEYS = ( "page", "rendition", "mime", "width", "height", "bytes", "sha256" )
#: Seconds waited before each retry of a transient failure: four attempts, under 15 s in all, well
#: inside the task lease the manifest still has to present.
RETRY_WAITS = ( 2.0, 4.0, 8.0 )
#: The gateway's per-task limits (WorkerDownloads::MAX_FILES, MAX_TEXT = 204800 bytes), with room to spare.
MAX_FILES = 20
TEXT_BUDGET = 200_000


def text_allowances( texts: list[ str ], budget: int = TEXT_BUDGET ) -> list[ int ]:
    """Bytes of UTF-8 text each file may send: smallest first, each taking its share of what is left."""
    sizes = [ len( t.encode( "utf-8" )) for t in texts ]
    out, left = [ 0 ] * len( texts ), budget
    order = sorted( range( len( texts )), key=lambda i: sizes[ i ])
    for n, i in enumerate( order ):
        out[ i ] = min( sizes[ i ], left // ( len( order ) - n ))
        left -= out[ i ]
    return out


def clip_utf8( text: str, limit: int ) -> str:
    data = text.encode( "utf-8" )
    return text if len( data ) <= limit else data[ :limit ].decode( "utf-8", "ignore" )


def _transient( exc: Exception ) -> bool:
    return isinstance( exc, GatewayError ) and ( exc.status == 0 or exc.status == 429 or exc.status >= 500 )


class DownloadPublisher:
    def __init__( self, uploader: IDownloadUploader, read: Callable[[ Path ], bytes ] = Path.read_bytes,
                  sleep: Callable[[ float ], None ] = time.sleep ) -> None:
        self._uploader, self._read, self._sleep = uploader, read, sleep

    def publish( self, task: ClaimedTask, details: dict ) -> dict:
        """`details` with each file given a `file_id` (and `inspectable`), or the whole set marked
        `inspectable: false` with the reason. Never raises for a gateway or file problem."""
        everything = [ f for f in details.get( "files" ) or [] if isinstance( f, dict )]
        if not everything:
            return details
        files, held = everything[ :MAX_FILES ], everything[ MAX_FILES: ]
        kept_back = [{ **self._summary( f ), "published": False,
                       "note": f"not published: one download publishes at most { MAX_FILES } files; it is on the "
                               f"desktop at { f.get( 'path' )}" } for f in held ]
        for attempt in range( 1, len( RETRY_WAITS ) + 2 ):
            try:
                sent, answer = self._send_all( task, files )
                break
            except ( GatewayError, OSError, ValueError ) as exc:
                if _transient( exc ) and attempt <= len( RETRY_WAITS ):
                    self._sleep( RETRY_WAITS[ attempt - 1 ])
                    continue
                tries = f" after { attempt } attempts" if attempt > 1 else ""
                reason = f"{ type( exc ).__name__ }: { str( exc )[ :240 ]}{ tries }"
                return { **details, "files": [ self._summary( f ) for f in files ] + kept_back, "inspectable": False,
                         "inspect_problem": f"the file was saved but could not be published for viewing ({ reason })" }
        ids = { int( row.get( "idx", -1 )): str( row.get( "file_id", "" ))
                for row in answer.get( "files" ) or [] if isinstance( row, dict )}
        out = []
        for index, f in enumerate( files ):
            summary = self._summary( f )
            summary[ "file_id" ] = ids.get( index, "" )
            summary[ "original_stored" ] = sent[ index ][ "original_stored" ]
            out.append( summary )
        inspectable = all( f[ "file_id" ] for f in out )
        return { **details, "files": out + kept_back, "inspectable": inspectable }

    def _send_all( self, task: ClaimedTask, files: list[ dict ]) -> tuple[ list[ dict ], dict ]:
        """Every blob, then the one manifest that names them. Safe to repeat: see the module docstring."""
        texts = [ str(( f.get( "inspection" ) or {}).get( "text" ) or "" )[ :MAX_TEXT ] if isinstance( f.get( "inspection" ), dict )
                  else "" for f in files ]
        allow = text_allowances( texts )
        sent = [ self._upload( task, index, f, allow[ index ]) for index, f in enumerate( files )]
        answer = self._uploader.post_downloads( task.task_id, { "task_id": task.task_id,
                                                                "lease_token": task.lease_token, "files": sent })
        return sent, answer

    def _upload( self, task: ClaimedTask, index: int, f: dict, text_bytes: int = TEXT_BUDGET ) -> dict:
        inspection = f.get( "inspection" ) if isinstance( f.get( "inspection" ), dict ) else {}
        original_stored = False
        if 0 < int( f.get( "bytes" ) or 0 ) <= MAX_BLOB:
            self._blob( task, Path( str( f[ "path" ])), str( f[ "sha256" ]))
            original_stored = True
        pages = []
        for page in inspection.get( "pages" ) or []:
            self._blob( task, Path( str( page[ "path" ])), str( page[ "sha256" ]))
            pages.append({ k: page.get( k ) for k in PAGE_KEYS })
        whole = str( inspection.get( "text" ) or "" )
        text = clip_utf8( whole[ :MAX_TEXT ], text_bytes )
        return { "idx": index, "name": f.get( "name" ), "original_name": f.get( "original_name" ),
                 "saved_path": f.get( "chrome_path" ) or f.get( "path" ), "mime": f.get( "mime" ),
                 "bytes": f.get( "bytes" ), "sha256": f.get( "sha256" ), "original_stored": original_stored,
                 "kind": inspection.get( "kind" ) or "binary", "page_count": inspection.get( "page_count" ) or 0,
                 "text": text, "text_truncated": bool( inspection.get( "text_truncated" )) or len( text ) < len( whole ),
                 "note": str( inspection.get( "note" ) or "" )[ :500 ], "pages": pages }

    def _blob( self, task: ClaimedTask, path: Path, sha256: str ) -> None:
        data = self._read( path )
        if hashlib.sha256( data ).hexdigest() != sha256:
            raise ValueError( f"{ path.name } changed after it was checked, so it is not published under that checksum" )
        if len( data ) > MAX_BLOB:
            raise ValueError( f"{ path.name } is larger than the gateway accepts" )
        self._uploader.upload_download_blob( task.task_id, sha256, data )

    @staticmethod
    def _summary( f: dict ) -> dict:
        """The file as the task record keeps it: everything but the text and the local rendition paths."""
        inspection = f.get( "inspection" ) if isinstance( f.get( "inspection" ), dict ) else {}
        out = { k: v for k, v in f.items() if k != "inspection" }
        if inspection:
            renditions = sorted({ str( p.get( "rendition" )) for p in inspection.get( "pages" ) or []})
            out[ "inspection" ] = { "kind": inspection.get( "kind" ), "page_count": inspection.get( "page_count" ) or 0,
                                    "renditions": renditions, "text_chars": len( str( inspection.get( "text" ) or "" )),
                                    "note": str( inspection.get( "note" ) or "" )[ :300 ]}
        return out
