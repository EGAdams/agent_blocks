import type { DetailTabKey } from "../catalog/tab.js";
import type { NavigationReader } from "../nav/nav-state.js";

/** Deliberate browser-test API published by the composition root as window.__spa. */
export interface SpaTestSeam {
    readonly navigation: NavigationReader;
    goHome(): void;
    goUp(): void;
    selectSection( top: string ): void;
    selectNode( key: string ): void;
    selectDetail( key: DetailTabKey ): void;
}

declare global {
    interface Window {
        __spa: SpaTestSeam;
    }
}
