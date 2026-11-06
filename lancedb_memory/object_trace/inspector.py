"""Inspector: the one read model behind the debugger UI and Toyota's questions.

Everything here is computed at read time from bounded queries. An answer
carries the evidence it stands on and a `missing` list for whatever no record
can show -- it never fills a gap with a guess, and an object is only called
alive when its last event is recent enough (see `liveness.py`).
"""
from __future__ import annotations

from collections.abc import Callable

from .interfaces import ITraceClock, ITraceReader
from .liveness import local_pid_alive, view
from .models import Answer, Evidence, Health, ObjectView, TraceObject
from .adapters.sssf_reader import SssfReader

NOTES_KIND  = "agent.notes"
RENDER_KIND = "browser.render"
TASK_KIND   = "bridge.task_run"
WRITES      = ( "marker_write", "marker_clear" )


def _get( data: dict | None, *path: str ) -> object:
    for key in path:
        if not isinstance( data, dict ):
            return None
        data = data.get( key )
    return data


def _yes( flag: object ) -> str:
    return "yes" if flag else "NO"


class Inspector:
    def __init__( self, reader: ITraceReader, clock: ITraceClock, sssf: SssfReader | None = None,
                  pid_alive: Callable[[ str, int | None ], bool | None ] = local_pid_alive ) -> None:
        self._reader = reader
        self._clock  = clock
        self._sssf   = sssf
        self._pid    = pid_alive

    # -- the UI's reads -------------------------------------------------------------

    def health( self, build: str, store: str ) -> Health:
        objects, events, max_seq, last = self._reader.counts()
        return Health( ok=True, build=build, schema_version=self._reader.schema_version(), objects=objects, events=events,
                       max_seq=max_seq, last_event_at=last, now=self._clock.now(), store=store,
                       sssf=self._sssf.status() if self._sssf else "absent" )

    def view( self, object_id: str, events: int = 50 ) -> ObjectView | None:
        now = self._clock.now()
        if object_id.startswith( "sssf.run:" ) and self._sssf:
            obj = self._sssf.session( object_id.split( ":", 1 )[ 1 ])
            if obj is None:
                return None
            phases = self._sssf.phases( obj.adw_id or "" )
            return view( obj, now, dependents=[ p.object_id for p in phases ], pid_alive=self._pid )
        obj = self._reader.object( object_id )
        if obj is None:
            return None
        return view( obj, now, self._reader.latest_events( object_id, events ), self._reader.dependents( object_id ), self._pid )

    def lite( self, obj: TraceObject ) -> dict:
        v = view( obj, self._clock.now(), pid_alive=self._pid )
        return { **obj.model_dump( exclude={ "inputs", "outputs" }), "display": v.display, "alive": v.alive,
                 "age_seconds": v.age_seconds, "reason": v.reason }

    def snapshot( self, *, kind: str | None = None, state: str | None = None, text: str | None = None,
                  run_id: str | None = None, task_id: str | None = None, limit: int = 300, sssf: int = 25 ) -> dict:
        objs = self._reader.objects( kind=kind, state=state, text=text, run_id=run_id, task_id=task_id, limit=limit )
        groups: dict[ str, dict ] = {}
        for obj in objs:
            key = obj.run_id or ( f"adw:{ obj.adw_id }" if obj.adw_id else f"process:{ obj.process_session or '?' }" )
            g = groups.setdefault( key, { "run_id": key, "first_at": obj.created_at, "last_at": 0.0, "objects": []})
            g[ "objects" ].append( self.lite( obj ))
            g[ "first_at" ] = min( g[ "first_at" ], obj.created_at )
            g[ "last_at" ]  = max( g[ "last_at" ], obj.last_event_at or obj.created_at )
        for g in groups.values():
            g[ "objects" ].sort( key=lambda o: ( o[ "created_at" ], o[ "object_id" ]))
        runs = sorted( groups.values(), key=lambda g: -g[ "last_at" ])
        adw  = [ self.lite( s ) for s in self._sssf.sessions( limit=sssf )] if self._sssf and sssf and not ( kind or text or run_id or task_id ) else []
        return { "now": self._clock.now(), "max_seq": self._reader.counts()[ 2 ], "runs": runs, "sssf_sessions": adw }

    def changes( self, after_seq: int, limit: int = 200 ) -> dict:
        events  = self._reader.events( after_seq=after_seq, limit=limit )
        touched = list( dict.fromkeys( e.object_id for e in events ))[ :100 ]
        objects = [ self.lite( o ) for o in ( self._reader.object( oid ) for oid in touched ) if o is not None ]
        return { "now": self._clock.now(), "max_seq": events[ -1 ].seq if events else after_seq,
                 "events": [ e.model_dump() for e in events ], "objects": objects, "more": len( events ) >= limit }

    # -- Toyota's four questions -------------------------------------------------------

    def ask( self, question: str, subject: str | None = None ) -> Answer:
        return {
            "latest_notes_update": lambda: self.latest_notes_update(),
            "where_stopped":       lambda: self.where_stopped( subject ),
            "object_state":        lambda: self.object_state( subject or "" ),
            "saved_and_rendered":  lambda: self.saved_and_rendered( subject ),
        }.get( question, lambda: Answer( question=question, found=False, answer=f"I don't know the question { question!r}.",
                                         missing=[ "a known question" ]))()

    def latest_notes_update( self ) -> Answer:
        q = "Show the latest Notes update"
        write = self._latest_write()
        if write is None:
            return Answer( question=q, found=False, answer="There is no traced Notes update to show.",
                           missing=[ "a Bridge notes-agent write (only those are traced; a note edited by hand on the "
                                     "Notes page leaves no object here)" ])
        v       = view( write, self._clock.now(), self._reader.latest_events( write.object_id, 10 ), pid_alive=self._pid )
        details = _get( write.outputs, "details" ) or {}
        op      = _get( write.inputs, "request", "notes" )
        when    = v.reason
        if op == "marker_clear":
            removed = _get( details, "removed" ) or []
            text = ( f"The latest Notes update was Bridge task { write.task_id }, a diagnostic-note clear, { v.display } "
                     f"({ when }); it removed { len( removed )} note(s)." )
        else:
            text = ( f"The latest Notes update was Bridge task { write.task_id }, { v.display } ({ when }): "
                     f"{ _get( details, 'filename' ) or 'no file named' } at revision "
                     f"{ str( _get( details, 'revision_after' ) or '?' )[ :12 ]}; read back from the store "
                     f"{ _yes( _get( details, 'store_marker' ))}, from the served endpoint { _yes( _get( details, 'served_marker' ))}, "
                     f"revisions { 'agree' if _get( details, 'revisions_agree' ) else 'do NOT agree' }." )
        missing = [] if write.outputs else [ "the write's outcome (the object has no recorded output)" ]
        return Answer( question=q, found=True, answer=text, subject=write.object_id, missing=missing,
                       evidence=self._evidence( v ))

    def where_stopped( self, task_id: str | None ) -> Answer:
        q = "Where did this task stop?"
        runs = self._reader.objects( kind=TASK_KIND, task_id=task_id, limit=1 ) if task_id else self._reader.objects( kind=TASK_KIND, limit=1 )
        if not runs:
            return Answer( question=q, found=False, subject=task_id,
                           answer=f"There is no traced Bridge task{ ' ' + task_id if task_id else '' }.",
                           missing=[ f"a bridge.task_run object{ ' for ' + task_id if task_id else '' } -- tasks run before "
                                     "tracing was installed have none" ])
        task = runs[ 0 ]
        now  = self._clock.now()
        tv   = view( task, now, self._reader.latest_events( task.object_id, 20 ), pid_alive=self._pid )
        phases = sorted( self._reader.objects( kind="adw.phase", text=f"adw.phase:{ task.adw_id }:", limit=50 ),
                         key=lambda o: o.created_at ) if task.adw_id else []
        evidence, missing = self._evidence( tv ), []
        stop = None
        for p in phases:
            pv = view( p, now, pid_alive=self._pid )
            evidence.append( Evidence( source="object_trace", ref=p.object_id, at=p.last_event_at,
                                       fact=f"phase { p.phase }: { pv.display } -- { pv.reason }" ))
            if pv.display not in ( "succeeded", ):
                stop = stop or ( p, pv )
        last_phase = phases[ -1 ] if phases else None
        if not phases:
            missing.append( "phase records for this run (the worker's ADW phases are traced only once instrumented)" )
        gateway = _get( task.outputs, "gateway_state" )
        summary = str( _get( task.outputs, "summary" ) or ( task.error or {}).get( "message", "" ))[ :200 ]
        if stop is not None:
            p, pv = stop
            where = f"stopped at phase { p.phase } ({ pv.display }: { pv.reason })"
        elif last_phase is not None:
            where = f"ran every phase through { last_phase.phase }"
        else:
            where = f"is { tv.display }"
        agent = self._reader.objects( kind="agent.*", task_id=task.task_id, limit=5 )
        if agent:
            av = view( agent[ 0 ], now, pid_alive=self._pid )
            evidence.append( Evidence( source="object_trace", ref=agent[ 0 ].object_id, at=agent[ 0 ].last_event_at,
                                       fact=f"agent { agent[ 0 ].kind }: { av.display } -- { av.reason }" ))
        submits = self._reader.objects( kind="toyota.bridge_submit", task_id=task.task_id, limit=1 )
        origin = f" Toyota submitted it in turn { submits[ 0 ].run_id }." if submits else ""
        if not submits:
            missing.append( "a Toyota submission record (it may have come from ChatGPT or a direct client)" )
        text = ( f"Bridge task { task.task_id } attempt { task.attempt } { where }. The task itself is { tv.display } "
                 f"({ tv.reason }){ ', gateway state ' + str( gateway ) if gateway else '' }"
                 f"{ ': ' + summary if summary else '' }.{ origin }" )
        return Answer( question=q, found=True, answer=text[ :1200 ], subject=task.object_id, evidence=evidence[ :20 ], missing=missing )

    def object_state( self, object_id: str ) -> Answer:
        q = "Show this object's state and error"
        v = self.view( object_id, 10 ) if object_id else None
        if v is None:
            return Answer( question=q, found=False, subject=object_id or None,
                           answer=f"There is no object { object_id!r} in the trace." if object_id else "Which object? Give me its id.",
                           missing=[ "an object with that id" ])
        o = v.object
        error = ( o.error or {}).get( "message" ) if o.error else None
        text = ( f"{ o.object_id } ({ o.kind }, { o.implementation or 'implementation not recorded' }"
                 f"{ ', interface ' + o.interface if o.interface else '' }) is { v.display }: { v.reason }."
                 f"{ ' Error: ' + str( error )[ :300 ] if error else ' No error recorded.' }" )
        missing = [] if o.interface else [ "the interface it implements (not recorded for this object)" ]
        return Answer( question=q, found=True, answer=text[ :1200 ], subject=o.object_id, evidence=self._evidence( v ), missing=missing )

    def saved_and_rendered( self, task_id: str | None ) -> Answer:
        q = "Did the content save, and did the browser render that version?"
        write = self._latest_write( task_id )
        if write is None:
            return Answer( question=q, found=False, subject=task_id,
                           answer="There is no traced Notes write to check.", missing=[ "a Bridge notes-agent write" ])
        d        = _get( write.outputs, "details" ) or {}
        filename = _get( d, "filename" )
        revision = _get( d, "revision_after" )
        saved    = bool( _get( d, "read_back_ok" )) and bool( _get( d, "revisions_agree" ))
        wv       = view( write, self._clock.now(), pid_alive=self._pid )
        evidence = self._evidence( wv )
        missing: list[ str ] = []
        renders = [ r for r in self._reader.objects( kind=RENDER_KIND, limit=50 )
                    if ( _get( r.inputs, "filename" ) or _get( r.outputs, "filename" )) == filename and r.created_at >= ( write.last_event_at or write.created_at ) - 1 ]
        save_line = ( f"Saved: { 'yes' if saved else 'NO' } -- { filename or '?' } at revision { str( revision or '?' )[ :12 ]}, "
                      f"store and served endpoint { 'agree' if _get( d, 'revisions_agree' ) else 'disagree' }." )
        if not renders:
            missing.append( "a browser render check after this write (run adws/adw_notes_render_check.py)" )
            return Answer( question=q, found=True, subject=write.object_id, evidence=evidence, missing=missing,
                           answer=f"{ save_line } Rendered: unknown -- no browser has reported rendering it since the write." )
        r = sorted( renders, key=lambda o: o.created_at )[ -1 ]
        rv = view( r, self._clock.now(), pid_alive=self._pid )
        evidence += self._evidence( rv )
        shown = _get( r.outputs, "revision" )
        match = bool( shown ) and shown == revision
        render_line = ( f"Rendered: { 'yes, that version' if match else 'NO' } -- { r.implementation or 'a browser' } showed "
                        f"revision { str( shown or 'none' )[ :12 ]}, marker visible { _yes( _get( r.outputs, 'marker_seen' ))} ({ rv.reason })." )
        if not match and r.state == "failed":
            render_line += f" The render check failed: { ( r.error or {}).get( 'message', '' )[ :200 ]}"
        return Answer( question=q, found=True, subject=write.object_id, evidence=evidence, missing=missing,
                       answer=f"{ save_line } { render_line }" )

    # -- plumbing -------------------------------------------------------------------------

    def _latest_write( self, task_id: str | None = None ) -> TraceObject | None:
        for obj in self._reader.objects( kind=NOTES_KIND, task_id=task_id, limit=50 ):
            if _get( obj.inputs, "request", "notes" ) in WRITES:
                return obj
        return None

    def _evidence( self, v: ObjectView ) -> list[ Evidence ]:
        o = v.object
        out = [ Evidence( source="object_trace", ref=o.object_id, at=o.last_event_at,
                          fact=f"{ o.kind } { o.implementation } is { v.display }: { v.reason }"[ :400 ])]
        for e in v.events[ -4: ]:
            out.append( Evidence( source="object_trace", ref=f"event { e.seq }", at=e.at, fact=f"{ e.kind }: { e.message }"[ :400 ]))
        return out
