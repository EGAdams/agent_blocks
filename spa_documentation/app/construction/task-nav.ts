import type { ConstructionTask } from "./task-model.js";
import type { Unsubscribe } from "../nav/nav-state.js";

export interface TaskNavigationSnapshot {
    readonly path: ReadonlyArray<string>;
    readonly focusedTask: ConstructionTask | null;
}

export interface TaskNavigationReader {
    snapshot(): TaskNavigationSnapshot;
    subscribe( listener: ( snapshot: TaskNavigationSnapshot ) => void ): Unsubscribe;
}

export interface TaskNavigationCommands {
    select( task: ConstructionTask ): void;
    goUp(): void;
    reset(): void;
}

export interface TaskNavigationStore extends
    TaskNavigationReader,
    TaskNavigationCommands {}

/** Observer subject for task drill-down only; it never renders navigation. */
export declare function createTaskNavigationStore(): TaskNavigationStore;
