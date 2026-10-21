import type { ContentEnhancer } from "./content-enhancer.js";

/** Executes every handler in insertion order; subsets are not exposed. */
export declare function createEnhancerPipeline(
    enhancers: ReadonlyArray<ContentEnhancer>,
): ContentEnhancer;
