import type { LessonActionGateway } from "../../core/docs-api.js";
import type { ContentEnhancer } from "./content-enhancer.js";

/** Concrete Strategy configuration for run-test-suite controls. */
export declare function createRunTestsButtonEnhancer(
    actions: LessonActionGateway,
): ContentEnhancer;
