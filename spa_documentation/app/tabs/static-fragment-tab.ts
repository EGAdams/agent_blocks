import type {
    DetailTabDescriptor,
} from "../catalog/tab.js";
import type { FragmentSource } from "../content/fragment-source.js";
import type { DetailTabStrategy } from "./tab-strategy.js";

/** Strategy for tabs whose only behavior is loading one HTML fragment. */
export declare function createStaticFragmentTabStrategy(
    descriptor: DetailTabDescriptor,
    fragments: FragmentSource,
): DetailTabStrategy;
