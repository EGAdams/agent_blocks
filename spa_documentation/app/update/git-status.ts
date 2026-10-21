import type { ItemKey } from "../catalog/document-path.js";
import type { GitStatusResponse } from "../core/api-contracts.js";
import type { SourceStatusGateway } from "../core/docs-api.js";
import type { SourceStatusCache } from "./status-cache.js";

/** Null Object used only when source status cannot be determined. */
export const UNKNOWN_STATUS: GitStatusResponse = Object.freeze( {
    item: "",
    exists: false,
    dirty: false,
    docs_missing: false,
} );

/** Coordinates one status refresh; storage and presentation remain separate. */
export interface GitStatusController {
    refresh( itemKey: ItemKey ): Promise<GitStatusResponse>;
}

export declare function createGitStatusController(
    gateway: SourceStatusGateway,
    cache: SourceStatusCache,
): GitStatusController;
