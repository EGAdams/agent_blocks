export interface MermaidTheme {
    readonly theme: string;
    readonly themeVariables: Readonly<Record<string, string>>;
}

/** Pure theme data copied verbatim from the legacy app.js during migration. */
export declare const MERMAID_THEME: MermaidTheme;
