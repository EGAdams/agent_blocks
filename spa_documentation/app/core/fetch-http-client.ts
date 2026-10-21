import type { HttpClient } from "./http-client.js";

export type FetchFunction = (
    input: RequestInfo | URL,
    init?: RequestInit,
) => Promise<Response>;

/** The only application adapter permitted to call the browser fetch API. */
export declare function createFetchHttpClient( fetchFunction: FetchFunction ): HttpClient;
