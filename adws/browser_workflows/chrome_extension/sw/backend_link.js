// BackendLink -- the one WebSocket to the recorder service in WSL.
//
// One job: stay connected, and carry JSON both ways. It knows nothing about
// recording. Messages sent while disconnected wait in chrome.storage.session
// (the service worker can be stopped at any moment; module variables can't
// be trusted to survive) and are flushed, in order, on reconnect.

const OUTBOX_KEY       = "bw_outbox";
const PING_MS          = 20000;     // under Chrome's 30s idle limit: WebSocket traffic keeps the worker alive (Chrome 116+)
const RECONNECT_MIN_MS = 1000;
const RECONNECT_MAX_MS = 15000;

export class BackendLink {
    constructor( url ) {
        this._url       = url;
        this._socket    = null;
        this._handlers  = [];
        this._openers   = [];
        this._ping      = null;
        this._delay     = RECONNECT_MIN_MS;
        this._reconnect = null;
    }

    get connected() {
        return this._socket !== null && this._socket.readyState === WebSocket.OPEN;
    }

    onMessage( handler ) { this._handlers.push( handler ); }

    onOpen( handler ) { this._openers.push( handler ); }

    connect() {
        if ( this._socket && this._socket.readyState <= WebSocket.OPEN ) return;
        let socket;
        try {
            socket = new WebSocket( this._url );
        } catch ( err ) {
            this._scheduleReconnect();
            return;
        }
        this._socket = socket;
        socket.onopen = async () => {
            this._delay = RECONNECT_MIN_MS;
            this._ping  = setInterval( () => this._sendNow( { type: "ping" } ), PING_MS );
            for ( const h of this._openers ) await h();
            await this._flush();
        };
        socket.onmessage = ( msg ) => {
            let data;
            try { data = JSON.parse( msg.data ); } catch ( err ) { return; }
            if ( data.type === "pong" ) return;
            for ( const h of this._handlers ) h( data );
        };
        socket.onclose = () => {
            clearInterval( this._ping );
            this._socket = null;
            this._scheduleReconnect();
        };
        socket.onerror = () => { /* onclose follows and reconnects */ };
    }

    // Sent now if connected, otherwise queued until the next connect.
    async send( message ) {
        if ( this.connected ) {
            this._sendNow( message );
            return;
        }
        const box = ( await chrome.storage.session.get( OUTBOX_KEY ) )[ OUTBOX_KEY ] || [];
        box.push( message );
        await chrome.storage.session.set( { [ OUTBOX_KEY ]: box.slice( -500 ) } );
        this.connect();
    }

    // Only when connected; a reply nobody is waiting for any more is not worth queuing.
    reply( message ) {
        if ( this.connected ) this._sendNow( message );
    }

    _sendNow( message ) {
        this._socket.send( JSON.stringify( message ) );
    }

    async _flush() {
        const box = ( await chrome.storage.session.get( OUTBOX_KEY ) )[ OUTBOX_KEY ] || [];
        if ( !box.length ) return;
        await chrome.storage.session.remove( OUTBOX_KEY );
        for ( const m of box ) this._sendNow( m );
    }

    _scheduleReconnect() {
        if ( this._reconnect ) return;
        this._reconnect = setTimeout( () => {
            this._reconnect = null;
            this.connect();
        }, this._delay );
        this._delay = Math.min( this._delay * 2, RECONNECT_MAX_MS );
    }
}
