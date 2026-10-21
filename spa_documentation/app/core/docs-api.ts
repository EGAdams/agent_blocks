import type {
    ActionResponse,
    GitStatusResponse,
    UpdateStartResponse,
    UpdateStatusResponse,
} from "./api-contracts.js";
import type { ApiDecoders } from "./api-decoders.js";
import type { HttpClient } from "./http-client.js";
import type { ItemKey } from "../catalog/document-path.js";

export interface HtmlDocumentGateway {
    readHtml( relativePath: string ): Promise<string>;
}

export interface SourceStatusGateway {
    readSourceStatus( itemKey: ItemKey ): Promise<GitStatusResponse>;
}

export interface DocumentationUpdateGateway {
    startUpdate( itemKey: ItemKey ): Promise<UpdateStartResponse>;
    readUpdateStatus( itemKey: ItemKey ): Promise<UpdateStatusResponse>;
}

export interface LessonActionGateway {
    invokeLessonAction( relativeApiPath: string ): Promise<ActionResponse>;
}

export interface DocsApi extends
    HtmlDocumentGateway,
    SourceStatusGateway,
    DocumentationUpdateGateway,
    LessonActionGateway {}

export declare function createDocsApi(
    httpClient: HttpClient,
    decoders: ApiDecoders,
): DocsApi;
