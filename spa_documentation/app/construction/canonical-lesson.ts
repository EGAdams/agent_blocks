import type { ContentEnhancer } from "../content/enhancers/content-enhancer.js";
import type { CanonicalLessonSource } from "./canonical-lesson-source.js";
import type { LessonUrlRebaser } from "./url-rebaser.js";

/** Coordinates canonical lesson loading, URL rebasing, insertion, and enhancement. */
export interface CanonicalLessonRenderer {
    render( focus: HTMLElement, sourcePath: string ): Promise<void>;
}

export declare function createCanonicalLessonRenderer(
    source: CanonicalLessonSource,
    rebaser: LessonUrlRebaser,
    enhancer: ContentEnhancer,
): CanonicalLessonRenderer;
