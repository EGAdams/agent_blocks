import type { ContentEnhancer } from "../content/enhancers/content-enhancer.js";
import type { CanonicalLessonRenderer } from "./canonical-lesson.js";
import type { ConstructionLessonBuilderFactory } from "./lesson-builder.js";
import type { ConstructionTask } from "./task-model.js";
import type { ConstructionSummaryRenderer } from "./summary.js";

/** Orchestrates which construction presentation is shown; it builds no markup. */
export interface ConstructionController {
    showLanding( container: HTMLElement ): void;
    showTask( focus: HTMLElement, task: ConstructionTask ): Promise<void>;
}

export declare function createConstructionController(
    summary: ConstructionSummaryRenderer,
    lessons: ConstructionLessonBuilderFactory,
    canonicalLessons: CanonicalLessonRenderer,
    enhancer: ContentEnhancer,
): ConstructionController;
