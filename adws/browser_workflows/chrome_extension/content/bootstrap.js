// Content bootstrap -- the page side's composition root. Injected last, it
// builds the capture once per document and starts or stops it on the service
// worker's word.
( () => {
    const bw = ( globalThis.__bw = globalThis.__bw || {} );
    if ( bw.booted ) return;
    bw.booted = true;

    let capture = null;
    const overlay = new bw.RecordingOverlay();

    chrome.runtime.onMessage.addListener( ( m ) => {
        if ( m.type === "bw_begin" ) {
            if ( !capture ) capture = new bw.EventCapture( new bw.SensitivityPolicy( m.rules ), overlay );
            capture.start( ( event ) => chrome.runtime.sendMessage( { type: "bw_captured", event } ) );
            overlay.show();
        } else if ( m.type === "bw_end" ) {
            if ( capture ) capture.stop();
            overlay.hide();
        }
    } );
} )();
