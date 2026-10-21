import type { DiagramInteraction } from "./diagram-interaction.js";

/** Owns only viewport transforms and pointer/wheel event wiring. */
export declare function createPanZoomInteraction(): DiagramInteraction;
