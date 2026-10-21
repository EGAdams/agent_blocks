import type { MermaidRuntime } from "./runtime.js";

export interface MermaidLoader {
    load(): Promise<MermaidRuntime>;
}

/** Lazy-loads vendor/mermaid.min.js and adapts window.mermaid. */
export declare function createMermaidLoader(
    window: Window,
    document: Document,
    scriptPath: string,
): MermaidLoader;
