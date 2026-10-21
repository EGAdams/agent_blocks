import type { DetailTabDescriptor } from "./tab.js";

export const OVERVIEW_FILE = "basic_agent.html" as const;

/** Pure tab metadata. Behavior belongs to the Strategy registry. */
export declare const detailTabs: ReadonlyArray<DetailTabDescriptor>;
