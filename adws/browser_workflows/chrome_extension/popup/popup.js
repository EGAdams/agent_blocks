// Popup -- Start / Stop / "Save As?". One job: show the state and pass the
// person's choice to the service worker, which passes it to the service.

const $ = ( id ) => document.getElementById( id );

async function ask( type, extra = {} ) {
    return chrome.runtime.sendMessage( { type, ...extra } );
}

function render( s ) {
    const words = {
        idle:      "Not recording.",
        recording: `Recording — ${ s.eventCount || 0 } actions so far.`,
        saving:    "Stopped. Name it to save it, or discard it.",
    };
    $( "status" ).textContent = s.connected === false
        ? "The recorder service is not running in WSL. Start it from the Browser Automation Workspace menu."
        : ( s.message ? `${ words[ s.state ] || s.state } ${ s.message }` : words[ s.state ] || s.state );
    $( "idle" ).hidden      = s.state !== "idle" || s.connected === false;
    $( "recording" ).hidden = s.state !== "recording";
    $( "saving" ).hidden    = s.state !== "saving" || s.connected === false;
}

async function refresh() { render( await ask( "popup_status" ) ); }

$( "start" ).addEventListener( "click", async () => { render( await ask( "popup_start" ) ); setTimeout( refresh, 800 ); } );
$( "stop" ).addEventListener( "click", async () => render( await ask( "popup_stop" ) ) );
$( "discard" ).addEventListener( "click", async () => { render( await ask( "popup_discard" ) ); setTimeout( refresh, 500 ); } );
$( "saving" ).addEventListener( "submit", async ( e ) => {
    e.preventDefault();
    const name = $( "name" ).value.trim();
    if ( !name ) return;
    render( await ask( "popup_save", { name } ) );
    setTimeout( refresh, 500 );
} );

refresh();
setInterval( refresh, 1500 );
