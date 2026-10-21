export interface GitStatusResponse {
    readonly item: string;
    readonly exists: boolean;
    readonly dirty: boolean;
    readonly docs_missing: boolean;
}

export type UpdateStartResponse =
    | { readonly ok: true }
    | { readonly ok: false; readonly error: string };

export interface UpdateStatusResponse {
    readonly running: boolean;
    readonly exit_code: number | null;
    readonly log_tail: string;
}

export type ActionResponse =
    | {
        readonly ok: true;
        readonly terminal?: string;
        readonly profile?: string;
        readonly directory?: string;
    }
    | { readonly ok: false; readonly error: string };
