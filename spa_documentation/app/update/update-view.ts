import type { GitStatusResponse } from "../core/api-contracts.js";
import type { UpdateRunState } from "./update-run.js";

export interface UpdateViewModel {
    readonly sourceStatus: GitStatusResponse | null;
    readonly runState: UpdateRunState;
    readonly start: () => void;
}

/** Passive DOM view for update controls and logs. */
export interface UpdateView {
    render( model: UpdateViewModel ): void;
}

export declare function createDomUpdateView( contentElement: HTMLElement ): UpdateView;
