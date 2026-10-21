import type { DocumentPathResolver } from "../catalog/document-path.js";
import type { DocumentTree } from "../catalog/doc-tree.js";
import type { TabStrategyRegistry } from "../tabs/tab-registry.js";
import type { NavigationSnapshot } from "../nav/nav-state.js";
import type { ContentHost } from "./content-host.js";
import type { FragmentSource } from "./fragment-source.js";

/** Application coordinator for choosing and displaying one content fragment. */
export interface ContentController {
    show( snapshot: NavigationSnapshot ): Promise<void>;
}

export declare function createContentController(
    host: ContentHost,
    fragments: FragmentSource,
    tree: DocumentTree,
    paths: DocumentPathResolver,
    tabs: TabStrategyRegistry,
): ContentController;
