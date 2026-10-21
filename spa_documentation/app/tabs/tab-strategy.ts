import type { DetailTabDescriptor } from "../catalog/tab.js";
import type { ContentHost } from "../content/content-host.js";
import type { NavigationReader } from "../nav/nav-state.js";

export interface TabActivationContext {
    readonly contentHost: ContentHost;
    readonly navigation: NavigationReader;
}

/** Strategy: one activation policy per detail tab. */
export interface DetailTabStrategy {
    readonly descriptor: DetailTabDescriptor;
    activate( context: TabActivationContext ): Promise<void>;
}
