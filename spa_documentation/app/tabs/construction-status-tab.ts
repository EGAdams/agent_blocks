import type {
    DetailTabDescriptor,
} from "../catalog/tab.js";
import type { ConstructionController } from "../construction/construction-controller.js";
import type { FragmentSource } from "../content/fragment-source.js";
import type { TaskNavigationCommands } from "../construction/task-nav.js";
import type { DetailTabStrategy } from "./tab-strategy.js";

/** Strategy for loading and initializing Construction Status task navigation. */
export declare function createConstructionStatusTabStrategy(
    descriptor: DetailTabDescriptor,
    fragments: FragmentSource,
    construction: ConstructionController,
    taskNavigation: TaskNavigationCommands,
): DetailTabStrategy;
