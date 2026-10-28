// SensitivityPolicy (page side) -- the same rules file Python reads, so a
// field the page calls secret is secret on both sides. One job: say why a
// field must not be recorded, or "".
( () => {
    const bw = ( globalThis.__bw = globalThis.__bw || {} );
    if ( bw.SensitivityPolicy ) return;

    bw.fieldHints = function ( el ) {
        const hints = {};
        const put = ( k, v ) => { if ( v ) hints[ k ] = String( v ).trim().slice( 0, 120 ); };
        put( "name", el.getAttribute( "name" ) );
        put( "id", el.id );
        put( "autocomplete", el.getAttribute( "autocomplete" ) );
        put( "aria", el.getAttribute( "aria-label" ) );
        put( "placeholder", el.getAttribute( "placeholder" ) );
        const label = ( el.labels && el.labels[ 0 ] ) ? el.labels[ 0 ].innerText : "";
        put( "label", label );
        return hints;
    };

    bw.SensitivityPolicy = class {
        constructor( rules ) {
            this._types    = new Set( rules.input_types.map( ( t ) => t.toLowerCase() ) );
            this._tokens   = new Set( rules.autocomplete_tokens.map( ( t ) => t.toLowerCase() ) );
            this._prefixes = rules.autocomplete_prefixes.map( ( p ) => p.toLowerCase() );
            // Python's \b and these patterns agree; the rules use no Python-only syntax.
            this._pattern  = new RegExp( rules.hint_pattern, "i" );
        }

        reason( inputType, hints ) {
            const type = ( inputType || "" ).toLowerCase();
            if ( this._types.has( type ) ) return `input type '${ type }'`;
            for ( const token of ( hints.autocomplete || "" ).toLowerCase().split( /\s+/ ) ) {
                if ( !token ) continue;
                if ( this._tokens.has( token ) || this._prefixes.some( ( p ) => token.startsWith( p ) ) ) {
                    return `autocomplete '${ token }'`;
                }
            }
            for ( const key of Object.keys( hints ).sort() ) {
                if ( key !== "autocomplete" && hints[ key ] && this._pattern.test( hints[ key ] ) ) return `${ key } looks sensitive`;
            }
            return "";
        }
    };
} )();
