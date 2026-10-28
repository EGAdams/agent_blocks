// RecorderState -- what this extension is recording, kept in
// chrome.storage.session so a restarted service worker picks it back up.
// One job: hold and persist the state. Deciding transitions is the service's.

const STATE_KEY = "bw_state";

const IDLE = Object.freeze( { state: "idle", sessionId: null, tabIds: [], eventCount: 0, message: "" } );

export class RecorderState {
    async get() {
        return ( await chrome.storage.session.get( STATE_KEY ) )[ STATE_KEY ] || { ...IDLE };
    }

    async set( patch ) {
        const next = { ...( await this.get() ), ...patch };
        await chrome.storage.session.set( { [ STATE_KEY ]: next } );
        return next;
    }

    async reset( message = "" ) {
        await chrome.storage.session.set( { [ STATE_KEY ]: { ...IDLE, message } } );
        return { ...IDLE, message };
    }

    // The tabs a recording may watch: the one the person chose, and tabs opened from it.
    async isAuthorized( tabId ) {
        const s = await this.get();
        return s.state === "recording" && s.tabIds.includes( tabId );
    }

    async authorize( tabId ) {
        const s = await this.get();
        if ( !s.tabIds.includes( tabId ) ) await this.set( { tabIds: [ ...s.tabIds, tabId ] } );
    }

    async countEvent() {
        const s = await this.get();
        return this.set( { eventCount: s.eventCount + 1 } );
    }
}
