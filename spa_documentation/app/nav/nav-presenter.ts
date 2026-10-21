import type { DocumentTree } from "../catalog/doc-tree.js";
import type { SectionCatalog } from "../catalog/sections.js";
import type { TabStrategyRegistry } from "../tabs/tab-registry.js";
import type { TaskNavigationContribution } from "../construction/task-nav-presenter.js";
import type { NavigationModel } from "./nav-model.js";
import type { NavigationCommands, NavigationSnapshot } from "./nav-state.js";

/** Presenter: converts state and catalogs into passive-view data. */
export interface NavigationPresenter {
    present( snapshot: NavigationSnapshot ): NavigationModel;
}

export declare function createNavigationPresenter(
    catalog: SectionCatalog,
    documentTree: DocumentTree,
    tabs: TabStrategyRegistry,
    commands: NavigationCommands,
    taskNavigation: TaskNavigationContribution,
): NavigationPresenter;
