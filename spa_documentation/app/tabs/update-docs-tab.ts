import type {
    DetailTabDescriptor,
} from "../catalog/tab.js";
import type { FragmentSource } from "../content/fragment-source.js";
import type { UpdateController } from "../update/update-controller.js";
import type { DetailTabStrategy } from "./tab-strategy.js";

/** Strategy for loading the report fragment and activating update controls. */
export declare function createUpdateDocsTabStrategy(
    descriptor: DetailTabDescriptor,
    fragments: FragmentSource,
    updates: UpdateController,
): DetailTabStrategy;
