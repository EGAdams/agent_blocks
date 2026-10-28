// The service worker's composition root: builds the pieces and wires Chrome's
// events to them. No behaviour lives here.

import { BackendLink } from "./backend_link.js";
import { BadgeIndicator } from "./indicator.js";
import { NavigationWatcher } from "./navigation_watcher.js";
import { PageInjector } from "./page_injector.js";
import { RecorderState } from "./recorder_state.js";
import { ExtensionRecordingController } from "./recording_controller.js";

const SERVICE_URL = "ws://127.0.0.1:8781/ws";
const WAKE_ALARM  = "bw_wake";

const link       = new BackendLink( SERVICE_URL );
const state      = new RecorderState();
const indicator  = new BadgeIndicator();
const injector   = new PageInjector( chrome.runtime.getURL( "sensitivity_rules.json" ) );
const controller = new ExtensionRecordingController( link, state, injector, indicator );
const watcher    = new NavigationWatcher( state, injector, ( tabId, event ) => controller.emit( tabId, event ) );

link.onMessage( ( m ) => controller.onServiceMessage( m ) );
link.onOpen( async () => {
    const s = await state.get();
    link.reply( { type: "hello", extension_version: chrome.runtime.getManifest().version, state: s.state,
                  session_id: s.sessionId } );
} );
watcher.install();

chrome.runtime.onMessage.addListener( ( m, sender, respond ) => {
    if ( m.type === "bw_captured" && sender.tab ) {
        controller.emit( sender.tab.id, m.event );
        return false;
    }
    if ( m.type === "bw_overlay_stop" ) {
        controller.onPopupMessage( { type: "popup_stop" } );
        return false;
    }
    if ( typeof m.type === "string" && m.type.startsWith( "popup_" ) ) {
        controller.onPopupMessage( m ).then( respond );
        return true;    // respond asynchronously
    }
    return false;
} );

// A stopped service worker is woken by the alarm and reconnects.
chrome.alarms.create( WAKE_ALARM, { periodInMinutes: 0.5 } );
chrome.alarms.onAlarm.addListener( ( a ) => { if ( a.name === WAKE_ALARM ) link.connect(); } );
chrome.runtime.onStartup.addListener( () => link.connect() );
chrome.runtime.onInstalled.addListener( () => link.connect() );

state.get().then( ( s ) => indicator.show( s.state ) );
link.connect();
