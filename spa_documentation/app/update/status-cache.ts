import type { ItemKey } from "../catalog/document-path.js";
import type { GitStatusResponse } from "../core/api-contracts.js";

/** Repository for status snapshots; it performs no network access. */
export interface SourceStatusCache {
    get( itemKey: ItemKey ): GitStatusResponse | null;
    set( itemKey: ItemKey, status: GitStatusResponse ): void;
    clear(): void;
}

export declare function createSourceStatusCache(): SourceStatusCache;
