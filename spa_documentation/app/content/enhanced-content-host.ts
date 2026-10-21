import type { ContentHost } from "./content-host.js";
import type { ContentEnhancer } from "./enhancers/content-enhancer.js";

/** Decorator that guarantees every successful insertion runs the full chain. */
export declare function createEnhancedContentHost(
    inner: ContentHost,
    enhancer: ContentEnhancer,
): ContentHost;
