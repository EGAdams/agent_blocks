import type { SpaApplication } from "./composition-root.js";
import type { SpaConfig } from "./config.js";

/** Entry-point seam; implementation will construct, expose, and start the app. */
export declare function bootstrap(
    window: Window,
    document: Document,
    config: SpaConfig,
): Promise<SpaApplication>;
