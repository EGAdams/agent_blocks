// Indicator -- the toolbar badge. One job: show the state where the person looks.

const LOOK = {
    recording: { text: "REC", color: "#c62828", title: "Browser Recorder: recording this tab" },
    saving:    { text: "SAVE", color: "#ef6c00", title: "Browser Recorder: stopped -- name it to save it" },
    idle:      { text: "", color: "#555555", title: "Browser Recorder" },
    offline:   { text: "OFF", color: "#757575", title: "Browser Recorder: recorder service not reachable" },
};

export class BadgeIndicator {
    async show( state ) {
        const look = LOOK[ state ] || LOOK.idle;
        await chrome.action.setBadgeText( { text: look.text } );
        await chrome.action.setBadgeBackgroundColor( { color: look.color } );
        await chrome.action.setTitle( { title: look.title } );
    }
}
