/** Strategy applied after Mermaid has produced an SVG. */
export interface DiagramInteraction {
    attach( figure: HTMLElement ): void;
}
