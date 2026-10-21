import type { ContentEnhancer } from "./content-enhancer.js";

/** Adapter for the browser rule that innerHTML does not execute script nodes. */
export declare function createScriptExecutionEnhancer(
    document: Document,
): ContentEnhancer;
