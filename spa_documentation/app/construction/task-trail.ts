import type { ConstructionTask } from "./task-model.js";
import type { ConstructionTaskTree } from "./task-tree.js";

/** Formats the reader-facing stage and step trail. */
export interface ConstructionTaskTrail {
    sentence( task: ConstructionTask ): string;
}

export declare function createConstructionTaskTrail(
    tree: ConstructionTaskTree,
): ConstructionTaskTrail;
