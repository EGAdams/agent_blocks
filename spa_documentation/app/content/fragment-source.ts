import type { HtmlDocumentGateway } from "../core/docs-api.js";

export interface FragmentSource {
    load( relativePath: string ): Promise<string>;
}

export declare function createFragmentSource(
    gateway: HtmlDocumentGateway,
): FragmentSource;
