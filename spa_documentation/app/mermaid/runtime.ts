import type { MermaidTheme } from "./theme.js";

export interface MermaidRenderResult {
    readonly svg: string;
}

/** Adapter port for the vendored global Mermaid object. */
export interface MermaidRuntime {
    initialize( configuration: MermaidTheme & { readonly startOnLoad: false } ): void;
    render( id: string, definition: string ): Promise<MermaidRenderResult>;
}
