import type { DetailTabKey } from "../catalog/tab.js";

export interface HashRoute {
    readonly top: string;
    readonly itemPath: ReadonlyArray<string>;
    readonly detail: DetailTabKey | null;
    readonly anchor: string | null;
}

/** Pure parser. It never reads window.location or mutates navigation. */
export interface HashRouteParser {
    parse( hash: string ): HashRoute | null;
}

export declare function createHashRouteParser(): HashRouteParser;
