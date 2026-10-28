// EventCapture -- the page side of IBrowserEventCapture. Listens in the
// capture phase and reports what the person did as raw events: one click,
// one text entry per field (on change, never per keystroke), one select,
// one check, one Enter. A secret field's value never leaves this function.
( () => {
    const bw = ( globalThis.__bw = globalThis.__bw || {} );
    if ( bw.EventCapture ) return;

    const CLICKABLE = "a, button, input, select, textarea, summary, label, [role=button], [role=link], [role=menuitem], " +
                      "[role=tab], [role=checkbox], [role=radio], [role=option], [onclick], [tabindex]";
    const TOGGLES   = [ "checkbox", "radio" ];
    const NOT_TEXT  = [ "submit", "button", "reset", "image", "file", ...TOGGLES ];

    const isTextField = ( el ) => el.tagName === "TEXTAREA" || ( el.tagName === "INPUT" && !NOT_TEXT.includes( el.type ) );

    bw.EventCapture = class {
        constructor( policy, overlay ) {
            this._policy    = policy;
            this._overlay   = overlay;
            this._emit      = null;
            this._committed = new WeakMap();      // field -> the value last reported
            this._onClick   = ( e ) => this._click( e );
            this._onChange  = ( e ) => this._change( e );
            this._onKey     = ( e ) => this._key( e );
        }

        start( emit ) {
            if ( this._emit ) return;
            this._emit = emit;
            window.addEventListener( "click", this._onClick, true );
            window.addEventListener( "change", this._onChange, true );
            window.addEventListener( "keydown", this._onKey, true );
        }

        stop() {
            window.removeEventListener( "click", this._onClick, true );
            window.removeEventListener( "change", this._onChange, true );
            window.removeEventListener( "keydown", this._onKey, true );
            this._emit = null;
        }

        // -- handlers --------------------------------------------------------

        _click( e ) {
            if ( !e.isTrusted || this._fromOverlay( e ) ) return;
            const el = e.target instanceof Element ? ( e.target.closest( CLICKABLE ) || e.target ) : null;
            if ( !el ) return;
            if ( el.tagName === "LABEL" && el.control ) return;    // the control's own click follows
            this._send( "click", el, { text: bw.visibleText( el ) } );
        }

        _change( e ) {
            if ( !e.isTrusted || this._fromOverlay( e ) ) return;
            const el = e.target;
            if ( !( el instanceof Element ) ) return;
            if ( el.tagName === "SELECT" ) {
                const option = el.selectedOptions[ 0 ];
                this._send( "select", el, { value: el.value, text: option ? option.text.trim() : "" } );
            } else if ( el.tagName === "INPUT" && TOGGLES.includes( el.type ) ) {
                this._send( "check", el, { checked: el.checked } );
            } else if ( isTextField( el ) ) {
                this._commitText( el );
            }
        }

        _key( e ) {
            if ( !e.isTrusted || e.key !== "Enter" || this._fromOverlay( e ) ) return;
            const el = e.target;
            if ( !( el instanceof Element ) || el.tagName === "TEXTAREA" ) return;
            if ( isTextField( el ) ) this._commitText( el );    // Enter can submit before change fires
            this._send( "key", el, { key: "Enter" } );
        }

        // -- helpers ---------------------------------------------------------

        _commitText( el ) {
            if ( this._committed.get( el ) === el.value ) return;
            this._committed.set( el, el.value );
            this._send( "text_committed", el, { value: el.value } );
        }

        _send( kind, el, extra ) {
            if ( !this._emit ) return;
            const hints  = bw.fieldHints( el );
            const reason = this._policy.reason( el.type || "", hints );
            const event  = {
                kind,
                url:              location.href,
                title:            document.title,
                timestamp:        Date.now() / 1000,
                tag:              el.tagName.toLowerCase(),
                input_type:       el.tagName === "INPUT" ? el.type : "",
                text:             "",
                field_hints:      hints,
                locators:         bw.describeLocators( el ),
                sensitive:        Boolean( reason ),
                sensitive_reason: reason,
                ...extra,
            };
            if ( reason && kind !== "click" && kind !== "key" ) {
                event.value = null;         // the secret stays on the page
                event.text  = "";
            }
            this._emit( event );
        }

        _fromOverlay( e ) {
            const host = this._overlay && this._overlay.host;
            return Boolean( host ) && e.composedPath().includes( host );
        }
    };
} )();
