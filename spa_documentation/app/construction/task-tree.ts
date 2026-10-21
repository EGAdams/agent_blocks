import type { ConstructionTask, ConstructionTaskReader } from "./task-model.js";

/** Composite traversal over construction-task markup. */
export interface ConstructionTaskTree {
    roots( container: HTMLElement ): ReadonlyArray<ConstructionTask>;
    children( task: ConstructionTask ): ReadonlyArray<ConstructionTask>;
    resolve(
        container: HTMLElement,
        path: ReadonlyArray<string>,
    ): ConstructionTask | null;
    ancestors( task: ConstructionTask ): ReadonlyArray<ConstructionTask>;
    siblings(
        task: ConstructionTask,
        container: HTMLElement,
    ): ReadonlyArray<ConstructionTask>;
}

export declare function createConstructionTaskTree(
    reader: ConstructionTaskReader,
): ConstructionTaskTree;
