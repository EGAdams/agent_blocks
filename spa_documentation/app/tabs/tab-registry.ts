import type { DetailTabKey } from "../catalog/tab.js";
import type { DetailTabStrategy } from "./tab-strategy.js";

export interface TabStrategyRegistry {
    all(): ReadonlyArray<DetailTabStrategy>;
    get( key: DetailTabKey ): DetailTabStrategy | null;
}

export declare function createTabStrategyRegistry(
    strategies: ReadonlyArray<DetailTabStrategy>,
): TabStrategyRegistry;
