import type { NavigationCommands, Unsubscribe } from "../nav/nav-state.js";
import type { HashRouteParser } from "./hash-route.js";

/** Adapter between browser hash events and navigation commands. */
export interface HashRouter {
    start(): Unsubscribe;
}

export declare function createHashRouter(
    window: Window,
    parser: HashRouteParser,
    navigation: NavigationCommands,
): HashRouter;
