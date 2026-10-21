export interface DocumentLeaf {
    readonly label: string;
    readonly items?: never;
}

export interface DocumentBranch {
    readonly label: string;
    readonly items: Readonly<Record<string, DocumentNode>>;
}

export type DocumentNode = DocumentLeaf | DocumentBranch;
export type SectionCatalog = Readonly<Record<string, DocumentNode>>;

/** Pure data copied verbatim from the legacy app.js during migration. */
export declare const sections: SectionCatalog;
