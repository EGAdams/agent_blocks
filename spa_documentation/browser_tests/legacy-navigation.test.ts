import assert from "node:assert/strict";
import { after, before, describe, test } from "node:test";

import { createLegacyNavigationEnvironment } from "./composition-root/composition-root.js";
import {
    CONVERSATION_STATUS_ANCHOR_HASH,
    CONVERSATION_STATUS_HASH,
    DETAIL_NAV_LABELS,
    DETAIL_TAB_DIRECTORY,
    DETAIL_TABS,
    EMBEDDED_LESSONS,
    FIRST_TASK_LINK,
    HOME_NAV_LABELS,
    SECOND_TASK_LINK,
    UNKNOWN_ITEM_HASH,
} from "./nav-fixtures/nav-fixtures.js";
import type { SpaPage } from "./spa-page/spa-page.js";

const environment = createLegacyNavigationEnvironment();

/** Every test ends the same way: acknowledge the step, then release the page. */
async function withSpaPage( body: ( spa: SpaPage ) => Promise<void> ): Promise<void> {
    const spa = await environment.newPage();
    try {
        await body( spa );
    } finally {
        await spa.finishStep();
        await spa.dispose();
    }
}

function recordScrollTargets(): void {
    ( window as unknown as Record<string, unknown> ).__lastScrolledId = null;
    Element.prototype.scrollIntoView = function scrollIntoView(): void {
        ( window as unknown as Record<string, unknown> ).__lastScrolledId = this.id || null;
    };
}

describe( "legacy SPA navigation characterization", { concurrency: false }, () => {
    before( async () => {
        await environment.start();
    });

    after( async () => {
        await environment.stop();
    });

    test( "renders the established home navigation in order", async () => {
        await withSpaPage( async ( spa ) => {
            await spa.openHome();
            await spa.expectNav( HOME_NAV_LABELS );
            await spa.expectActiveNav( "Home" );
        });
    });

    test( "drills down to a leaf and preserves all five detail tabs", async () => {
        await withSpaPage( async ( spa ) => {
            await spa.openHome();
            await spa.clickLink( "Catherine Agent" );
            await spa.clickLink( "Basic Agent" );
            await spa.expectNav( DETAIL_NAV_LABELS );

            for ( const tab of DETAIL_TABS ) {
                assert.equal( await spa.loadDetailTab( tab, DETAIL_TAB_DIRECTORY ), 200 );
                assert.deepEqual( await spa.navLabels(), DETAIL_NAV_LABELS );
                assert.notEqual( await spa.contentHtml(), "" );
            }

            await spa.content( ".update-doc-bar" ).waitFor();
        });
    });

    test( "applies a deep-linked tab and scrolls to its anchor", async () => {
        const spa = await environment.newInstrumentedPage( recordScrollTargets );
        try {
            await spa.openHash( CONVERSATION_STATUS_ANCHOR_HASH );
            await spa.waitForCondition(
                () =>
                    ( window as unknown as Record<string, unknown> ).__lastScrolledId
                    === "next-steps",
            );
            await spa.content( "#next-steps" ).waitFor();
            await spa.waitForLink( FIRST_TASK_LINK );
        } finally {
            await spa.finishStep();
            await spa.dispose();
        }
    });

    test( "falls back to Home for an unknown deep link", async () => {
        await withSpaPage( async ( spa ) => {
            await spa.openHash( UNKNOWN_ITEM_HASH );
            await spa.expectNav( HOME_NAV_LABELS );
            await spa.expectActiveNav( "Home" );
        });
    });

    test( "hides the in-page header in embedded mode", async () => {
        await withSpaPage( async ( spa ) => {
            await spa.openEmbedded();
            assert.equal( await spa.body().getAttribute( "class" ), "embedded" );
            assert.equal(
                await spa.header().evaluate( ( header ) => getComputedStyle( header ).display ),
                "none",
            );
        });
    });

    test( "drills into both embedded lessons and backs out one level at a time", async () => {
        await withSpaPage( async ( spa ) => {
            await spa.openHash( CONVERSATION_STATUS_HASH );
            await spa.waitForLink( FIRST_TASK_LINK );
            await spa.clickLink( FIRST_TASK_LINK );

            for ( const lesson of EMBEDDED_LESSONS ) {
                await spa.clickLink( lesson.navLabel );
                await spa.waitForHeading( lesson.heading );
                await spa.waitForCondition(
                    () =>
                        document.querySelectorAll( "#content .construction-task-focus svg" )
                            .length === 2,
                );
                assert.equal(
                    await spa.content( "[data-open-project-terminal]" )
                        .getAttribute( "data-project-terminal-wired" ),
                    "true",
                );
                assert.equal(
                    await spa.content( "[data-run-test-suite]" )
                        .getAttribute( "data-run-tests-wired" ),
                    "true",
                );
            }

            await spa.clickLink( "Back" );
            await spa.waitForLink( SECOND_TASK_LINK );
            await spa.clickLink( "Back" );
            await spa.expectNav( DETAIL_NAV_LABELS );
        });
    });
});
