export interface SpaConfig {
    readonly docsIndexPath: string;
    readonly mermaidScriptPath: string;
    readonly updatePollIntervalMilliseconds: number;
}

/** Runtime configuration values; no feature module reads process globals. */
export declare const DEFAULT_SPA_CONFIG: SpaConfig;
