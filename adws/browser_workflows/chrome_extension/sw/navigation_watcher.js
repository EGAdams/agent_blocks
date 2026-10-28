// NavigationWatcher -- turns Chrome's tab and navigation events, for the
// authorized tabs only, into raw events, and re-injects capture after each
// page load (a new document has no content script). One job: watch tabs.

export class NavigationWatcher {
    constructor( state, injector, emit ) {
        this._state    = state;
        this._injector = injector;
        this._emit     = emit;
    }

    install() {
        chrome.webNavigation.onCommitted.addListener( ( d ) => this._committed( d ) );
        chrome.webNavigation.onDOMContentLoaded.addListener( ( d ) => this._loaded( d ) );
        chrome.tabs.onCreated.addListener( ( tab ) => this._created( tab ) );
        chrome.tabs.onActivated.addListener( ( info ) => this._activated( info ) );
    }

    async _committed( d ) {
        if ( d.frameId !== 0 || !( await this._state.isAuthorized( d.tabId ) ) ) return;
        if ( !/^https?:/i.test( d.url ) ) return;
        let title = "";
        try { title = ( await chrome.tabs.get( d.tabId ) ).title || ""; } catch ( err ) { /* closed */ }
        const qualifiers = d.transitionQualifiers || [];
        const transition = qualifiers.includes( "server_redirect" ) ? "server_redirect"
                         : qualifiers.includes( "client_redirect" ) ? "client_redirect"
                         : qualifiers.includes( "forward_back" )    ? "forward_back"
                         : d.transitionType;
        await this._emit( d.tabId, { kind: "navigation", url: d.url, title, tab_id: d.tabId,
                                     transition_type: transition, timestamp: d.timeStamp / 1000 } );
    }

    async _loaded( d ) {
        if ( d.frameId !== 0 || !( await this._state.isAuthorized( d.tabId ) ) ) return;
        if ( !this._injector.constructor.canRecord( d.url ) ) return;
        try { await this._injector.begin( d.tabId ); } catch ( err ) { /* a page we may not script */ }
    }

    async _created( tab ) {
        if ( tab.openerTabId !== undefined && ( await this._state.isAuthorized( tab.openerTabId ) ) ) {
            await this._state.authorize( tab.id );
        }
    }

    async _activated( info ) {
        if ( !( await this._state.isAuthorized( info.tabId ) ) ) return;
        let tab;
        try { tab = await chrome.tabs.get( info.tabId ); } catch ( err ) { return; }
        await this._emit( info.tabId, { kind: "tab_switch", url: tab.url || "", title: tab.title || "",
                                        tab_id: info.tabId, timestamp: Date.now() / 1000 } );
    }
}
