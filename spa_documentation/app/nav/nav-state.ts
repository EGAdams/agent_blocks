import type { DetailTabKey } from "../catalog/tab.js";

export interface NavigationSnapshot {
    readonly currentTop: string;
    readonly itemPath: ReadonlyArray<string>;
    readonly currentDetail: DetailTabKey | null;
}

export type NavigationListener = ( snapshot: NavigationSnapshot ) => void;
export type Unsubscribe = () => void;

/** Observer read side. Views cannot mutate navigation. */
export interface NavigationReader {
    snapshot(): NavigationSnapshot;
    subscribe( listener: NavigationListener ): Unsubscribe;
}

/** Command side. Consumers receive only the mutations they need. */
export interface NavigationCommands {
    goHome(): void;
    selectSection( top: string ): void;
    selectNode( key: string ): void;
    goUp(): void;
    selectDetail( tabKey: DetailTabKey ): void;
}

export interface NavigationStore extends NavigationReader, NavigationCommands {}

export declare function createNavigationStore(): NavigationStore;
