// RecordingController (extension side) -- answers the service's commands and
// forwards what the pages captured. The service decides; this carries out.

import { PageInjector } from "./page_injector.js";

export class ExtensionRecordingController {
    constructor( link, state, injector, indicator ) {
        this._link      = link;
        this._state     = state;
        this._injector  = injector;
        this._indicator = indicator;
    }

    // -- from the service ----------------------------------------------------

    async onServiceMessage( m ) {
        if ( m.type === "start_recording" ) return this._start( m );
        if ( m.type === "stop_recording" ) return this._stop( m );
        if ( m.type === "state" ) return this._mirror( m );
    }

    async _start( m ) {
        const [ tab ] = await chrome.tabs.query( { active: true, lastFocusedWindow: true } );
        if ( !tab || !PageInjector.canRecord( tab.url ) ) {
            this._link.reply( { type: "recording_started", request_id: m.request_id, session_id: m.session_id,
                                error: `the selected tab (${ tab ? tab.url : "none" }) is not a web page` } );
            return;
        }
        await this._state.set( { state: "recording", sessionId: m.session_id, tabIds: [ tab.id ], eventCount: 0, message: "" } );
        try {
            await this._injector.begin( tab.id );
        } catch ( err ) {
            await this._state.reset();
            this._link.reply( { type: "recording_started", request_id: m.request_id, session_id: m.session_id,
                                error: `could not watch that tab: ${ err.message }` } );
            return;
        }
        await this._indicator.show( "recording" );
        this._link.reply( { type: "recording_started", request_id: m.request_id, session_id: m.session_id,
                            tab_id: tab.id, url: tab.url, title: tab.title || "" } );
        // The page the person is on is where playback starts.
        await this.emit( tab.id, { kind: "navigation", url: tab.url, title: tab.title || "", tab_id: tab.id,
                                   transition_type: "typed", timestamp: Date.now() / 1000 } );
    }

    async _stop( m ) {
        const s = await this._state.get();
        for ( const tabId of s.tabIds ) await this._injector.end( tabId );
        await this._state.set( { state: "saving", tabIds: [] } );
        await this._indicator.show( "saving" );
        if ( m.request_id ) {
            this._link.reply( { type: "recording_stopped", request_id: m.request_id, session_id: s.sessionId,
                                event_count: s.eventCount } );
        }
    }

    async _mirror( m ) {
        const s = await this._state.get();
        if ( m.state === "idle" ) {
            for ( const tabId of s.tabIds ) await this._injector.end( tabId );
            await this._state.reset( m.message || "" );
        } else {
            await this._state.set( { state: m.state, sessionId: m.session_id, eventCount: m.event_count || s.eventCount,
                                     message: m.message || "" } );
        }
        await this._indicator.show( m.state );
    }

    // -- from the pages ------------------------------------------------------

    async emit( tabId, event ) {
        const s = await this._state.get();
        if ( s.state !== "recording" || !s.tabIds.includes( tabId ) ) return;
        await this._state.countEvent();
        await this._link.send( { type: "event", session_id: s.sessionId, event } );
    }

    // -- from the popup ------------------------------------------------------

    async onPopupMessage( m ) {
        const s = await this._state.get();
        if ( m.type === "popup_status" ) return { ...s, connected: this._link.connected };
        if ( !this._link.connected ) return { ...s, connected: false, message: "the recorder service is not running in WSL" };
        if ( m.type === "popup_start" ) await this._link.send( { type: "start_requested" } );
        if ( m.type === "popup_stop" ) {
            await this._stop( {} );
            await this._link.send( { type: "stop_requested", session_id: s.sessionId } );
        }
        if ( m.type === "popup_save" ) await this._link.send( { type: "save_requested", session_id: s.sessionId, name: m.name } );
        if ( m.type === "popup_discard" ) await this._link.send( { type: "discard_requested", session_id: s.sessionId } );
        return { ...( await this._state.get() ), connected: true };
    }
}
