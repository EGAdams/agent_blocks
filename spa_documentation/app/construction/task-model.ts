export type ConstructionTaskStatus = "planned" | "current" | "done";

export interface ConstructionTask {
    readonly element: HTMLElement;
    readonly id: string;
    readonly label: string;
    readonly status: ConstructionTaskStatus;
    readonly title: string;
    readonly description: string;
    readonly lesson: HTMLElement | null;
}

/** Adapter over one li[data-task-id]. It performs no tree traversal. */
export interface ConstructionTaskReader {
    read( element: HTMLElement ): ConstructionTask;
}

export declare function createConstructionTaskReader(): ConstructionTaskReader;
