/**
 * The characterization fixtures: the exact navigation the legacy `app.js` is
 * expected to produce. Nothing here knows about Playwright or Node.
 */

export interface DetailTabFixture {
    readonly label: string;
    readonly fileName: string;
}

export interface LessonFixture {
    readonly navLabel: string;
    readonly heading: string;
}

export const HOME_NAV_LABELS: ReadonlyArray<string> = [
    "Home",
    "Claude Agent Adapter",
    "Catherine Agent",
    "Lancedb Memory",
    "Voice Communication",
    "Browser Tests",
    "Mazda",
    "Solid Agent Systems",
];

export const DETAIL_NAV_LABELS: ReadonlyArray<string> = [
    "Back",
    "Source",
    "Class Diagram",
    "Sequence Diagram",
    "Construction Status",
    "Update Documentation",
];

export const DETAIL_TAB_DIRECTORY = "/catherine_agent_sdk/basic_agent";

export const DETAIL_TABS: ReadonlyArray<DetailTabFixture> = [
    { label: "Source", fileName: "basic_agent.py.html" },
    { label: "Class Diagram", fileName: "mermaid_class.html" },
    { label: "Sequence Diagram", fileName: "mermaid_sequence.html" },
    { label: "Construction Status", fileName: "basic_agent_construction_status.html" },
    { label: "Update Documentation", fileName: "basic_agent_update_documentation.html" },
];

export const EMBEDDED_LESSONS: ReadonlyArray<LessonFixture> = [
    { navLabel: "Interface File", heading: "Interface File Construction Status" },
    { navLabel: "Event Contracts", heading: "Event Contracts Construction Status" },
];

export const CONVERSATION_STATUS_HASH =
    "#item=voice_communication/conversation_agent&tab=status";
export const CONVERSATION_STATUS_ANCHOR_HASH = `${CONVERSATION_STATUS_HASH}&anchor=next-steps`;
export const UNKNOWN_ITEM_HASH = "#item=missing/not_real&tab=source";
export const FIRST_TASK_LINK = "1. Declare the Plug-in Point";
export const SECOND_TASK_LINK = "2. Prove With One Adapter";
