import type { FragmentSource } from "../content/fragment-source.js";

/** Loads and extracts one canonical article; URL rewriting is not its job. */
export interface CanonicalLessonSource {
    load( relativePath: string ): Promise<HTMLElement>;
}

export declare function createCanonicalLessonSource(
    fragments: FragmentSource,
    document: Document,
): CanonicalLessonSource;
