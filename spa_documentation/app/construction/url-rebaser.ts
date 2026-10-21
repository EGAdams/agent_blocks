/** Rewrites only URL-bearing attributes on imported lesson markup. */
export interface LessonUrlRebaser {
    rebase( article: HTMLElement, sourceUrl: string ): void;
}

export declare function createLessonUrlRebaser(): LessonUrlRebaser;
