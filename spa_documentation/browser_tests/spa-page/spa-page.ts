import type { Locator, Page } from "playwright-core";

import type { DetailTabFixture } from "../nav-fixtures/nav-fixtures.js";
import type { NavigationProbe } from "../nav-probe/nav-probe.js";
import type { StepGate } from "../step-gate/step-gate.js";

/**
 * Page Object. Tests speak in SPA vocabulary — open a hash, click a nav link,
 * load a detail tab — and never touch a raw Playwright selector.
 */
export interface SpaPage {
    openHome(): Promise<void>;
    openHash( hash: string ): Promise<void>;
    openEmbedded(): Promise<void>;
    clickLink( name: string ): Promise<void>;
    waitForLink( name: string ): Promise<void>;
    waitForHeading( name: string ): Promise<void>;
    expectNav( labels: ReadonlyArray<string> ): Promise<void>;
    expectActiveNav( label: string ): Promise<void>;
    /** Clicks a detail tab and returns the HTTP status of the fetched document. */
    loadDetailTab( tab: DetailTabFixture, directory: string ): Promise<number>;
    navLabels(): Promise<ReadonlyArray<string>>;
    contentHtml(): Promise<string>;
    content( selector: string ): Locator;
    body(): Locator;
    header(): Locator;
    waitForCondition( predicate: () => boolean ): Promise<void>;
    finishStep(): Promise<void>;
    dispose(): Promise<void>;
}

export interface SpaPageDependencies {
    readonly page: Page;
    readonly baseUrl: string;
    readonly probe: NavigationProbe;
    readonly gate: StepGate;
    readonly dispose: () => Promise<void>;
}

export function createSpaPage( dependencies: SpaPageDependencies ): SpaPage {
    const { page, baseUrl, probe, gate } = dependencies;
    const indexUrl = `${baseUrl}/index.html`;

    function link( name: string ): Locator {
        return page.getByRole( "link", { name, exact: true });
    }

    return {
        async openHome(): Promise<void> {
            await page.goto( indexUrl );
        },

        async openHash( hash: string ): Promise<void> {
            await page.goto( `${indexUrl}${hash}` );
        },

        async openEmbedded(): Promise<void> {
            await page.goto( `${indexUrl}?embedded=1` );
        },

        async clickLink( name: string ): Promise<void> {
            await link( name ).click();
        },

        async waitForLink( name: string ): Promise<void> {
            await link( name ).waitFor();
        },

        async waitForHeading( name: string ): Promise<void> {
            await page.getByRole( "heading", { name, exact: true }).waitFor();
        },

        async expectNav( labels: ReadonlyArray<string> ): Promise<void> {
            await probe.expectLabels( page, labels );
        },

        async expectActiveNav( label: string ): Promise<void> {
            await probe.expectActive( page, label );
        },

        async loadDetailTab( tab: DetailTabFixture, directory: string ): Promise<number> {
            const responsePromise = page.waitForResponse( ( response ) =>
                response.url().endsWith( `${directory}/${tab.fileName}` ),
            );
            await link( tab.label ).click();
            const response = await responsePromise;
            await probe.expectActive( page, tab.label );
            return response.status();
        },

        async navLabels(): Promise<ReadonlyArray<string>> {
            return probe.labels( page );
        },

        async contentHtml(): Promise<string> {
            return page.locator( "#content" ).innerHTML();
        },

        content( selector: string ): Locator {
            return page.locator( `#content ${selector}` );
        },

        body(): Locator {
            return page.locator( "body" );
        },

        header(): Locator {
            return page.locator( "header" );
        },

        async waitForCondition( predicate: () => boolean ): Promise<void> {
            await page.waitForFunction( predicate );
        },

        async finishStep(): Promise<void> {
            await gate.waitForAcknowledgement( page );
        },

        dispose: dependencies.dispose,
    };
}
