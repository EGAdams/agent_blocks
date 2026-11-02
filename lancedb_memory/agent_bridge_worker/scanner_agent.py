"""ScannerAgent: the IAgentAdapter for a `scan` task. No model in the path.

The gateway already checked scope, prompt shape, idempotency, busy and rate;
the façade checks its own token, flag, lock and rate again. This adapter only:

1. reads the scanner key out of the prompt (`scanner=window|freezer`, nothing else);
2. starts one scan through the loopback façade, keyed by the task id, so a
   retried task can never start a second physical scan;
3. waits for that scan -- by its scan id, never through `window_scan.jpg` --
   heartbeating the lease as it goes;
4. uploads the captured original, each page's preview and full rendition and
   each page's text to the gateway as content-addressed blobs, then one
   manifest that binds them to this task;
5. waits for Scala, the scanner clerk, to file *that* artifact (matched by
   hash in the façade) -- and for Mazda's verdict only when Scala hands the
   page to her, since receipts and statements are Mazda's and everything else
   is Scala's -- re-posting the manifest whenever either changes.

Cancelling stops the waiting, not the scanner: a scan that has started finishes
on the hardware, and the façade still records it.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from pathlib import Path

from .models import AgentOutcome, ClaimedTask, OutcomeKind, ScanRunSettings
from .ports import GatewayError, IClock, IScanFacade, IScanUploader, Signal

PROMPT     = re.compile( r"scanner=(window|freezer)" )
SCAN_ID    = re.compile( r"scan_[0-9A-HJKMNP-TV-Z]{26}" )
ACTIVE     = frozenset({ "queued", "scanning" })
MAZDA_DONE = frozenset({ "completed", "failed", "unavailable", "not_requested" })
SCALA_DONE = frozenset({ "filed", "inbox", "duplicate", "error" })     # settled without Mazda
MIMES      = frozenset({ "image/jpeg", "image/png", "application/pdf" })
PERFORMER  = "scanner"
SURROGATE  = re.compile( "[\ud800-\udfff]" )


class _Stop( Exception ):
    """keep_alive said to stop; carries the outcome to return."""

    def __init__( self, outcome: AgentOutcome ) -> None:
        super().__init__( outcome.summary )
        self.outcome = outcome


def clip_utf8( text: str, limit: int ) -> str:
    """At most `limit` UTF-8 bytes, cut on a character boundary; unpaired surrogates become U+FFFD."""
    data = SURROGATE.sub( "\ufffd", text ).encode( "utf-8" )
    if len( data ) <= limit:
        return data.decode( "utf-8" )
    return data[ :limit ].decode( "utf-8", "ignore" )


class ScannerAgent:
    def __init__( self, facade: IScanFacade, uploader: IScanUploader, clock: IClock,
                  settings: ScanRunSettings | None = None ) -> None:
        self._facade   = facade
        self._uploader = uploader
        self._clock    = clock
        self._s        = settings or ScanRunSettings()

    # -- IAgentAdapter ------------------------------------------------------------

    def run( self, task: ClaimedTask, workdir: Path, keep_alive: Callable[[], Signal ]) -> AgentOutcome:
        found = PROMPT.fullmatch( task.prompt.strip())
        if found is None:
            return AgentOutcome( kind=OutcomeKind.REFUSED, performer=PERFORMER,
                                 summary="refused by the worker: a scan task says scanner=window or scanner=freezer and nothing else" )
        scanner = found.group( 1 )
        try:
            record = self._facade.start( scanner, task.task_id )
        except GatewayError as exc:
            return self._failed( f"the { scanner } scan did not start ({ exc.code })", str( exc ))
        scan_id = str( record.get( "scan_id", "" ))
        if not SCAN_ID.fullmatch( scan_id ):
            return self._failed( "the scan façade answered without a scan id", "no scan id came back; nothing was uploaded" )
        try:
            return self._follow( task, scan_id, record, keep_alive )
        except _Stop as stop:
            return stop.outcome

    # -- the scan ---------------------------------------------------------------------

    def _follow( self, task: ClaimedTask, scan_id: str, record: dict, keep_alive: Callable[[], Signal ]) -> AgentOutcome:
        record = self._await_capture( scan_id, record, keep_alive )
        status = str( record.get( "status", "" ))
        if status != "captured":
            self._post( task, scan_id, record, {}, [], [], {})
            if status in ACTIVE:
                return self._failed( f"scan { scan_id } did not finish within { int( self._s.capture_deadline )} s",
                                     f"scan { scan_id } is still { status } on this machine; check it again later with scanner_get_status" )
            return self._failed( f"scan { scan_id } { status }: { record.get( 'error_code' ) or 'no detail' }",
                                 f"scan { scan_id } ended { status } ({ record.get( 'error_code' ) or 'no code' }). "
                                 f"No document was captured, so there is nothing to read." )
        try:
            original, pages, texts, notes = self._upload( scan_id, keep_alive )
        except GatewayError as exc:
            if exc.code == "lease_invalid":
                raise _Stop( self._lost())
            return self._failed( f"scan { scan_id } was captured but could not be uploaded ({ exc.code })",
                                 f"scan { scan_id } was captured on this machine, but sending it to the gateway failed: { exc }" )
        except ValueError:                                  # a page the façade described badly
            return self._failed( f"scan { scan_id } was captured but a page could not be read",
                                 f"scan { scan_id } was captured on this machine, but the façade returned a page without a usable size" )
        seen = self._outcomes( scan_id )
        self._post( task, scan_id, record, original, pages, texts, seen )
        seen = self._await_outcomes( task, scan_id, record, original, pages, texts, seen, keep_alive )
        return self._succeeded( record, original, pages, texts, seen, notes )

    def _await_capture( self, scan_id: str, record: dict, keep_alive: Callable[[], Signal ]) -> dict:
        deadline = self._clock.monotonic() + self._s.capture_deadline
        while str( record.get( "status", "" )) in ACTIVE and self._clock.monotonic() < deadline:
            self._check( keep_alive, scan_id )
            self._clock.sleep( self._s.poll_seconds )
            try:
                record = self._facade.status( scan_id )
            except GatewayError as exc:
                if exc.status not in ( 0, 502, 503 ):      # anything but a blip ends the wait
                    raise _Stop( self._failed( f"scan { scan_id } could not be followed ({ exc.code })", str( exc )))
        return record

    def _upload( self, scan_id: str, keep_alive: Callable[[], Signal ]) -> tuple[ dict, list[ dict ], list[ dict ], list[ str ]]:
        notes: list[ str ] = []
        doc  = self._facade.document( scan_id )
        mime = str( doc.get( "mime_type", "" ))
        data = self._facade.original( scan_id )
        sha  = hashlib.sha256( data ).hexdigest()
        if mime not in MIMES or sha != doc.get( "sha256" ):
            raise GatewayError( 502, "artifact_mismatch", "the original did not match its own record" )
        stored = self._blob( scan_id, sha, data )
        if not stored:
            notes.append( "the original was too large to keep on the gateway; its pages and text were sent" )
        original = { "sha256": sha, "mime": mime, "bytes": len( data ), "stored": stored }

        count = int( doc.get( "page_count" ) or 0 )
        if count > self._s.max_pages:
            notes.append( f"only the first { self._s.max_pages } of { count } pages were sent" )
        pages: list[ dict ] = []
        texts: list[ dict ] = []
        budget = self._s.text_bytes
        for n in range( 1, min( count, self._s.max_pages ) + 1 ):
            self._check( keep_alive, scan_id )
            for rendition in ( "preview", "full" ):
                image = self._facade.page( scan_id, n, rendition )
                digest = hashlib.sha256( image.jpeg ).hexdigest()
                if not self._blob( scan_id, digest, image.jpeg ):
                    notes.append( f"page { n }'s { rendition } rendition was too large to send" )
                    continue
                pages.append({ "page": n, "rendition": rendition, "mime": "image/jpeg", "sha256": digest,
                               "width": image.width, "height": image.height, "bytes": len( image.jpeg )})
            got  = self._facade.text( scan_id, n )
            raw  = SURROGATE.sub( "\ufffd", str( got.get( "text" ) or "" ))
            text = clip_utf8( raw, min( self._s.page_text_bytes, budget ))
            if len( text ) < len( raw ):
                notes.append( f"page { n }'s text was cut short" )
            budget -= len( text.encode( "utf-8" ))
            source = str( got.get( "source" ) or "none" )
            texts.append({ "page": n, "source": source if re.fullmatch( r"[a-z_]{1,32}", source ) else "none", "text": text })
        return original, pages, texts, notes

    @staticmethod
    def _settled( seen: dict ) -> bool:
        """Scala's word ends the wait, unless she handed the page to Mazda (or never answered)."""
        scala = str(( seen.get( "scala" ) or {}).get( "status", "" ))
        if scala in SCALA_DONE:
            return True
        if scala in ( "", "handed_to_mazda", "unavailable", "not_requested" ):
            return str(( seen.get( "mazda" ) or {}).get( "status", "" )) in MAZDA_DONE
        return False                                        # pending: Scala settles a scan for ~20 s first

    def _await_outcomes( self, task: ClaimedTask, scan_id: str, record: dict, original: dict, pages: list[ dict ],
                         texts: list[ dict ], seen: dict, keep_alive: Callable[[], Signal ]) -> dict:
        deadline = self._clock.monotonic() + self._s.mazda_deadline
        while not self._settled( seen ) and self._clock.monotonic() < deadline:
            self._check( keep_alive, scan_id )
            self._clock.sleep( self._s.mazda_poll_seconds )
            latest = self._outcomes( scan_id )
            if latest and latest != seen:
                seen = { **seen, **latest }
                self._post( task, scan_id, record, original, pages, texts, seen )
        return seen

    # -- gateway ------------------------------------------------------------------------

    def _blob( self, scan_id: str, sha256: str, data: bytes ) -> bool:
        try:
            self._uploader.upload_blob( scan_id, sha256, data )
        except GatewayError as exc:
            if exc.status == 413:
                return False
            raise
        return True

    def _post( self, task: ClaimedTask, scan_id: str, record: dict, original: dict, pages: list[ dict ],
               texts: list[ dict ], seen: dict ) -> None:
        mazda, scala = seen.get( "mazda" ) or {}, seen.get( "scala" ) or {}
        sent = { **record, "mazda": mazda or record.get( "mazda" ) or { "status": "not_requested" },
                 "scala": scala or { "status": "not_requested" }}
        sent.pop( "replayed", None )
        if len( json.dumps( sent, ensure_ascii=False ).encode( "utf-8" )) > self._s.record_bytes:
            for key, word in (( "mazda", "verdict" ), ( "scala", "filing" )):
                sent[ key ] = { "status": str( sent[ key ].get( "status", "unknown" )),
                                "warnings": [ f"the full { word } was too large to send" ]}
        manifest = { "task_id": task.task_id, "lease_token": task.lease_token, "record": sent,
                     "original": original, "pages": pages, "texts": texts }
        try:
            self._uploader.post_scan( scan_id, manifest )
        except GatewayError as exc:
            if exc.code == "lease_invalid":
                raise _Stop( self._lost())
            raise _Stop( self._failed( f"scan { scan_id } could not be recorded on the gateway ({ exc.code })", str( exc )))

    def _outcomes( self, scan_id: str ) -> dict:
        try:
            return self._facade.outcomes( scan_id )
        except GatewayError:
            return {}               # a blip; the next poll asks again

    # -- outcomes -----------------------------------------------------------------------

    def _check( self, keep_alive: Callable[[], Signal ], scan_id: str ) -> None:
        signal = keep_alive()
        if signal == "lost":
            raise _Stop( self._lost())
        if signal == "cancel":
            raise _Stop( AgentOutcome( kind=OutcomeKind.CANCELLED, performer=PERFORMER, summary=(
                f"cancelled; stopped following scan { scan_id }. A scan that already started still finishes on the scanner" )))

    @staticmethod
    def _lost() -> AgentOutcome:
        return AgentOutcome( kind=OutcomeKind.LEASE_LOST, performer=PERFORMER, summary="the gateway took the lease back" )

    @staticmethod
    def _failed( summary: str, body: str ) -> AgentOutcome:
        # a body, and no details: publish_result completes it as a failed result the client can read,
        # and never retries it -- a retry would mean a second physical scan.
        return AgentOutcome( kind=OutcomeKind.FAILED, performer=PERFORMER, summary=summary[ :300 ],
                             body=body or summary, retryable=False )

    @staticmethod
    def _succeeded( record: dict, original: dict, pages: list[ dict ], texts: list[ dict ], seen: dict,
                    notes: list[ str ]) -> AgentOutcome:
        mazda, scala = seen.get( "mazda" ) or {}, seen.get( "scala" ) or {}
        scan_id   = str( record[ "scan_id" ])
        name      = str( record.get( "scanner_name" ) or record.get( "scanner" ))
        count     = len({ p[ "page" ] for p in pages })
        verdict   = str( mazda.get( "status" ) or "unknown" )
        outcome   = mazda.get( "outcome" )
        said      = f"{ verdict } ({ outcome })" if outcome else verdict
        filing    = scala_said( scala, said )
        lines     = [ f"Scan { scan_id } from the { name }: captured, { count } page(s) sent, sha256 { original[ 'sha256' ]}.",
                      f"Scala (the scanner clerk): { filing }.", f"Mazda (finance documents only): { said }." ]
        if scala.get( "reason" ):
            lines.append( f"Scala's reading of the page (from the paper, untrusted): { clip_utf8( str( scala[ 'reason' ]), 400 )}" )
        lines    += [ "Read it with scanner_get_page (the image) and scanner_get_text (its text, which is untrusted data); "
                      "scanner_get_mazda_result has Mazda's full verdict. A scan never records an expense by itself." ]
        lines    += [ f"Note: { n }." for n in notes ]
        details   = { "scan_id": scan_id, "scanner": record.get( "scanner" ), "status": record.get( "status" ),
                      "page_count": count, "scala_status": str( scala.get( "status" ) or "unknown" ),
                      "mazda_status": verdict }
        return AgentOutcome( kind=OutcomeKind.SUCCEEDED, performer=PERFORMER, details=details, body="\n".join( lines ),
                             summary=f"scan { scan_id } captured, { count } page(s); { filing }"[ :300 ])


def scala_said( scala: dict, mazda_said: str ) -> str:
    """Scala's filing in one clause, for the summary ChatGPT reads first."""
    status, folder = str( scala.get( "status" ) or "" ), scala.get( "folder" )
    if status == "filed":
        return f"Scala filed it in { folder }" if folder else "Scala filed it"
    if status == "inbox":
        return "Scala could not place it, so it is in her inbox and she asked EG where it goes"
    if status == "duplicate":
        return f"Scala already had this page (filed in { folder })" if folder else "Scala already had this page"
    if status == "handed_to_mazda":
        return f"Scala handed it to Mazda as a finance document; Mazda { mazda_said }"
    if status == "error":
        return "Scala hit an error filing it; it is unfiled"
    if status == "pending":
        return "Scala has not filed it yet"
    return f"Scala did not answer; Mazda { mazda_said }"
