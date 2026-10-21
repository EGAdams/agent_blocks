import type {
    ActionResponse,
    GitStatusResponse,
    UpdateStartResponse,
    UpdateStatusResponse,
} from "./api-contracts.js";

export interface Decoder<T> {
    decode( input: unknown ): T;
}

export interface ApiDecoders {
    readonly gitStatus: Decoder<GitStatusResponse>;
    readonly updateStart: Decoder<UpdateStartResponse>;
    readonly updateStatus: Decoder<UpdateStatusResponse>;
    readonly action: Decoder<ActionResponse>;
}

export declare function createApiDecoders(): ApiDecoders;
