import type { ClipboardWriter } from "../core/clipboard.js";
import type { ConstructionTask } from "./task-model.js";
import type { ConstructionStatusPolicy } from "./task-status.js";
import type { ConstructionTaskTrail } from "./task-trail.js";
import type { ConstructionTaskTree } from "./task-tree.js";

export interface ConstructionLessonBuilder {
    masthead(): this;
    keyIdea(): this;
    authoredSections(): this;
    roster(): this;
    counts(): this;
    continuation(): this;
    footer(): this;
    build(): HTMLElement;
}

export interface ConstructionLessonBuilderFactory {
    create( task: ConstructionTask ): ConstructionLessonBuilder;
}

/** Builder order remains explicit at the one orchestration call site. */
export declare function createConstructionLessonBuilderFactory(
    document: Document,
    tree: ConstructionTaskTree,
    statuses: ConstructionStatusPolicy,
    trail: ConstructionTaskTrail,
    clipboard: ClipboardWriter,
    docsIndexPath: string,
): ConstructionLessonBuilderFactory;
