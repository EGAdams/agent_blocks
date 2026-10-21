import type { LessonActionGateway } from "../../core/docs-api.js";
import type { ContentEnhancer } from "./content-enhancer.js";

/** Concrete Strategy configuration for project-terminal controls. */
export declare function createProjectTerminalButtonEnhancer(
    actions: LessonActionGateway,
): ContentEnhancer;
