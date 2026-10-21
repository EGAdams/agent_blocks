import assert from "node:assert/strict";
import type { Page } from "playwright-core";

/**
 * Every assertion about the `#nav` strip lives here, so the selector the
 * legacy `app.js` renders is named in exactly one place.
 */
export interface NavigationProbe {
    labels( page: Page ): Promise<ReadonlyArray<string>>;
    expectLabels( page: Page, expected: ReadonlyArray<string> ): Promise<void>;
    expectActive( page: Page, label: string ): Promise<void>;
}

const NAV_LINK_SELECTOR = "#nav a";
const ACTIVE_NAV_LINK_SELECTOR = "#nav a.active";

async function readLabels( page: Page ): Promise<ReadonlyArray<string>> {
    return page.locator( NAV_LINK_SELECTOR ).allTextContents();
}

export function createNavigationProbe(): NavigationProbe {
    return {
        labels: readLabels,

        async expectLabels( page: Page, expected: ReadonlyArray<string> ): Promise<void> {
            await page.waitForFunction( ( labels: ReadonlyArray<string> ) => {
                const actual = Array.from(
                    document.querySelectorAll( "#nav a" ),
                    ( link ) => link.textContent?.trim() ?? "",
                );
                return JSON.stringify( actual ) === JSON.stringify( labels );
            }, expected );
            assert.deepEqual( await readLabels( page ), expected );
        },

        async expectActive( page: Page, label: string ): Promise<void> {
            await page.locator( ACTIVE_NAV_LINK_SELECTOR, { hasText: label }).waitFor();
        },
    };
}
