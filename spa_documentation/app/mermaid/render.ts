import type { DiagramInteraction } from "./diagram-interaction.js";
import type { MermaidLoader } from "./loader.js";
import type { MermaidTheme } from "./theme.js";

export interface DiagramRenderer {
    render( figure: HTMLElement ): Promise<void>;
}

/** Coordinates loading, rendering, then post-render interaction Strategies. */
export declare function createDiagramRenderer(
    loader: MermaidLoader,
    theme: MermaidTheme,
    interactions: ReadonlyArray<DiagramInteraction>,
): DiagramRenderer;
