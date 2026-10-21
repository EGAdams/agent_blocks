import type { NavigationModel } from "../nav/nav-model.js";
import type { ConstructionTaskTree } from "./task-tree.js";
import type {
    TaskNavigationCommands,
    TaskNavigationReader,
} from "./task-nav.js";

export interface TaskNavigationContribution {
    active(): boolean;
    present(): NavigationModel | null;
}

/** Converts task state into nav items without mutating the DOM. */
export declare function createTaskNavigationPresenter(
    reader: TaskNavigationReader,
    commands: TaskNavigationCommands,
    tree: ConstructionTaskTree,
): TaskNavigationContribution;
