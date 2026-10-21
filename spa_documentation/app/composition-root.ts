import type { SpaConfig } from "./config.js";

export interface SpaApplication {
    start(): Promise<void>;
    dispose(): void;
}

/** Factory/composition root. Concrete browser adapters are selected only here. */
export declare function createSpaApplication(
    window: Window,
    document: Document,
    config: SpaConfig,
): SpaApplication;
