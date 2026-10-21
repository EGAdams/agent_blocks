import type { NavigationModel } from "./nav-model.js";

/** Passive view. It renders a supplied model and owns no navigation decisions. */
export interface NavigationView {
    render( model: NavigationModel ): void;
}

export declare function createDomNavigationView( navElement: HTMLElement ): NavigationView;
