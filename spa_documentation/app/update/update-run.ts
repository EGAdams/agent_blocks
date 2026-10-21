import type { ItemKey } from "../catalog/document-path.js";
import type { DocumentationUpdateGateway } from "../core/docs-api.js";
import type { Unsubscribe } from "../nav/nav-state.js";

export type UpdateRunState =
    | { readonly kind: "idle" }
    | { readonly kind: "starting"; readonly itemKey: ItemKey }
    | {
        readonly kind: "running";
        readonly itemKey: ItemKey;
        readonly logTail: string;
    }
    | {
        readonly kind: "completed";
        readonly itemKey: ItemKey;
        readonly exitCode: number;
        readonly logTail: string;
    }
    | { readonly kind: "failed"; readonly itemKey: ItemKey; readonly error: Error };

/** Finite-state machine with an exhaustive, immutable public snapshot. */
export interface UpdateRunMachine {
    snapshot(): UpdateRunState;
    subscribe( listener: ( state: UpdateRunState ) => void ): Unsubscribe;
    start( itemKey: ItemKey ): Promise<void>;
    dispose(): void;
}

export declare function createUpdateRunMachine(
    gateway: DocumentationUpdateGateway,
    pollIntervalMilliseconds: number,
): UpdateRunMachine;
