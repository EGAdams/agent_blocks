// PageInjector -- puts the capture scripts into a tab and tells them to start
// or stop. One job: the chrome.scripting plumbing.

const CONTENT_FILES = [
    "content/sensitivity.js",
    "content/locator_describer.js",
    "content/overlay.js",
    "content/capture.js",
    "content/bootstrap.js",
];

export class PageInjector {
    constructor( rulesUrl ) {
        this._rulesUrl = rulesUrl;
        this._rules    = null;
    }

    static canRecord( url ) {
        return /^https?:\/\//i.test( url || "" );
    }

    async begin( tabId ) {
        await chrome.scripting.executeScript( { target: { tabId }, files: CONTENT_FILES } );
        const rules = await this._loadRules();
        await chrome.tabs.sendMessage( tabId, { type: "bw_begin", rules } );
    }

    async end( tabId ) {
        try {
            await chrome.tabs.sendMessage( tabId, { type: "bw_end" } );
        } catch ( err ) {
            // The tab is gone or never had the script; nothing to stop.
        }
    }

    async _loadRules() {
        if ( !this._rules ) this._rules = await ( await fetch( this._rulesUrl ) ).json();
        return this._rules;
    }
}
