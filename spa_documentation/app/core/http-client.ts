export type HttpMethod = "GET" | "POST";

export interface HttpRequest {
    readonly method: HttpMethod;
    readonly headers?: Readonly<Record<string, string>>;
    readonly body?: string;
    readonly cache?: RequestCache;
}

export interface HttpResponse {
    readonly ok: boolean;
    readonly status: number;
    text(): Promise<string>;
    json(): Promise<unknown>;
}

export interface HttpClient {
    request( relativeUrl: string, request: HttpRequest ): Promise<HttpResponse>;
}
