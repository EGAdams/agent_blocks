// LocatorDescriber -- every recorded way of finding an element again, most
// durable first. One job: describe; it never decides what was done.
//
// Ranks (lower wins): test id 10, name 20, aria-label 30, <label> 35,
// visible text 40, placeholder 45, id 50 (80 if it looks generated), path 90.
( () => {
    const bw = ( globalThis.__bw = globalThis.__bw || {} );
    if ( bw.describeLocators ) return;

    const TEST_ATTRS = [ "data-testid", "data-test", "data-qa", "data-cy" ];
    const q = ( v ) => `"${ String( v ).replace( /\\/g, "\\\\" ).replace( /"/g, '\\"' ) }"`;

    const looksGenerated = ( id ) => /\d{3,}|[0-9a-f]{8,}|^(?:ember|react|mui|ng|:r)/i.test( id );

    bw.visibleText = function ( el ) {
        const t = ( el.innerText || el.value || "" ).replace( /\s+/g, " " ).trim();
        return t.length <= 60 ? t : "";
    };

    function scopeFor( el ) {
        const tag  = el.tagName.toLowerCase();
        const role = el.getAttribute( "role" );
        if ( role ) return `[role=${ q( role ) }]`;
        if ( tag === "input" ) return `input[type=${ q( el.type ) }]`;
        return tag;
    }

    function cssPath( el ) {
        if ( el === document.body ) return "body";
        const parts = [];
        for ( let node = el; node && node.nodeType === 1 && node !== document.body; node = node.parentElement ) {
            if ( node.id && !looksGenerated( node.id ) ) {
                parts.unshift( `#${ CSS.escape( node.id ) }` );
                break;
            }
            let index = 1;
            for ( let sib = node.previousElementSibling; sib; sib = sib.previousElementSibling ) {
                if ( sib.tagName === node.tagName ) index++;
            }
            parts.unshift( `${ node.tagName.toLowerCase() }:nth-of-type(${ index })` );
        }
        return parts.join( " > " );
    }

    bw.describeLocators = function ( el ) {
        const tag = el.tagName.toLowerCase();
        const out = [];
        const add = ( strategy, value, source, rank, scope = "" ) => {
            if ( value && !out.some( ( l ) => l.strategy === strategy && l.value === value && l.tag === scope ) ) {
                out.push( { strategy, value, source, rank, tag: scope } );
            }
        };
        for ( const attr of TEST_ATTRS ) {
            const v = el.getAttribute( attr );
            if ( v ) add( "css", `[${ attr }=${ q( v ) }]`, "test_id", 10 );
        }
        const name = el.getAttribute( "name" );
        if ( name ) add( "css", `${ tag }[name=${ q( name ) }]`, "name", 20 );
        const aria = el.getAttribute( "aria-label" );
        if ( aria ) add( "label", aria.trim(), "aria_label", 30, scopeFor( el ) );
        if ( el.labels && el.labels[ 0 ] ) add( "label", el.labels[ 0 ].innerText.replace( /\s+/g, " " ).trim(), "label", 35, scopeFor( el ) );
        const isField = tag === "textarea" || tag === "select" || ( tag === "input" && ![ "submit", "button", "reset", "image" ].includes( el.type ) );
        if ( !isField ) add( "text", bw.visibleText( el ), "text", 40, scopeFor( el ) );
        const ph = el.getAttribute( "placeholder" );
        if ( ph ) add( "css", `${ tag }[placeholder=${ q( ph ) }]`, "placeholder", 45 );
        if ( el.id ) add( "css", `#${ CSS.escape( el.id ) }`, "id", looksGenerated( el.id ) ? 80 : 50 );
        add( "css", cssPath( el ), "css_path", 90 );
        return out.sort( ( a, b ) => a.rank - b.rank );
    };
} )();
