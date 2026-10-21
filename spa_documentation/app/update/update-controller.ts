import type { ItemKey } from "../catalog/document-path.js";
import type { GitStatusController } from "./git-status.js";
import type { SourceStatusCache } from "./status-cache.js";
import type { UpdateRunMachine } from "./update-run.js";
import type { UpdateView } from "./update-view.js";

/** Mediator for status, run state, and the passive update view. */
export interface UpdateController {
    show( itemKey: ItemKey ): Promise<void>;
    dispose(): void;
}

export declare function createUpdateController(
    statuses: GitStatusController,
    statusCache: SourceStatusCache,
    run: UpdateRunMachine,
    view: UpdateView,
): UpdateController;
