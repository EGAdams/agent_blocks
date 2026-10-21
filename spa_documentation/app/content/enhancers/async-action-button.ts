import type { ActionResponse } from "../../core/api-contracts.js";
import type { ContentEnhancer } from "./content-enhancer.js";

export interface AsyncActionButtonCopy {
    readonly pendingButton: string;
    readonly pendingStatus: string;
    readonly successButton: ( idleLabel: string ) => string;
    readonly successStatus: ( result: ActionResponse ) => string;
    readonly failureStatus: ( error: Error ) => string;
}

export interface AsyncActionButtonSpec {
    readonly selector: string;
    readonly wiredDatasetKey: string;
    readonly statusSelector: string;
    readonly copy: AsyncActionButtonCopy;
    readonly invoke: ( relativeApiPath: string ) => Promise<ActionResponse>;
}

/** Reusable binder configured by immutable action-button Strategy data. */
export declare function createAsyncActionButtonEnhancer(
    spec: AsyncActionButtonSpec,
): ContentEnhancer;
