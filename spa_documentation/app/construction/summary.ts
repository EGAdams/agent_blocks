import type { ConstructionStatusPolicy } from "./task-status.js";
import type { ConstructionTaskTree } from "./task-tree.js";

export interface ConstructionSummaryRenderer {
    render( container: HTMLElement ): void;
}

export declare function createConstructionSummaryRenderer(
    tree: ConstructionTaskTree,
    statuses: ConstructionStatusPolicy,
): ConstructionSummaryRenderer;
