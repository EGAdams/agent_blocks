import type {
    DocumentBranch,
    DocumentNode,
    SectionCatalog,
} from "./sections.js";

export interface DocumentTree {
    resolve( top: string, path: ReadonlyArray<string> ): DocumentNode | null;
    isBranch( node: DocumentNode ): node is DocumentBranch;
}

/** Composite traversal only; URL and filesystem formatting live elsewhere. */
export declare function createDocumentTree( catalog: SectionCatalog ): DocumentTree;
