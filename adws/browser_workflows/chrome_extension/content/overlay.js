// RecordingOverlay -- the on-page "recording" banner with a Stop button. It
// lives in a closed shadow root, so the page can't restyle or read it, and
// capture ignores anything inside it. One job: be visible -- and never be in
// the way: the banner is click-through, only its Stop button takes a click,
// and it sits bottom-right, away from where sites put their menus and logins.
( () => {
    const bw = ( globalThis.__bw = globalThis.__bw || {} );
    if ( bw.RecordingOverlay ) return;

    bw.RecordingOverlay = class {
        constructor() { this._host = null; }

        get host() { return this._host; }

        show() {
            if ( this._host ) return;
            const host = document.createElement( "bw-recorder-overlay" );
            const root = host.attachShadow( { mode: "closed" } );
            root.innerHTML = `
              <style>
                .bar { position: fixed; bottom: 8px; right: 8px; z-index: 2147483647; display: flex; gap: 8px;
                       pointer-events: none; opacity: .92;
                       align-items: center; padding: 6px 10px; border-radius: 6px; background: #b71c1c; color: #fff;
                       font: 600 12px/1.2 system-ui, sans-serif; box-shadow: 0 2px 8px rgba(0,0,0,.3); }
                .dot { width: 8px; height: 8px; border-radius: 50%; background: #fff; animation: blink 1s infinite; }
                button { pointer-events: auto; font: inherit; border: 0; border-radius: 4px; padding: 3px 8px; cursor: pointer; }
                @keyframes blink { 50% { opacity: .2; } }
              </style>
              <div class="bar" role="status"><span class="dot"></span>Recording this tab<button type="button">Stop</button></div>`;
            root.querySelector( "button" ).addEventListener( "click", ( e ) => {
                e.stopPropagation();
                chrome.runtime.sendMessage( { type: "bw_overlay_stop" } );
            } );
            ( document.body || document.documentElement ).appendChild( host );
            this._host = host;
        }

        hide() {
            if ( this._host ) this._host.remove();
            this._host = null;
        }
    };
} )();
