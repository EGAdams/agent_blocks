import type { ConstructionTask, ConstructionTaskStatus } from "./task-model.js";

export interface ConstructionStatusCounts {
    readonly total: number;
    readonly done: number;
    readonly current: number;
    readonly planned: number;
}

/** Status wording and aggregation only. */
export interface ConstructionStatusPolicy {
    word( status: ConstructionTaskStatus ): string;
    counts( tasks: ReadonlyArray<ConstructionTask> ): ConstructionStatusCounts;
}

export declare function createConstructionStatusPolicy(): ConstructionStatusPolicy;
