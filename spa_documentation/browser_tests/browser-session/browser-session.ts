import { chromium } from "playwright-core";
import type { Browser, Page } from "playwright-core";

import type { PlatformProfile } from "../platform-profile/platform-profile.js";
import type { TestConfig } from "../test-config/test-config.js";

/** A page plus the teardown that owns it (the page, or its whole context). */
export interface ManagedPage {
    readonly page: Page;
    dispose(): Promise<void>;
}

/**
 * Facade over Playwright's browser lifecycle. It is the only module that
 * imports the launcher, so launch flags and window presentation live in one
 * place.
 */
export interface BrowserSession {
    open(): Promise<void>;
    newPage(): Promise<ManagedPage>;
    /** A page whose context runs `initScript` before any SPA code loads. */
    newInstrumentedPage( initScript: () => void ): Promise<ManagedPage>;
    close(): Promise<void>;
}

const HEADED_WINDOW_ARGS: ReadonlyArray<string> = [
    "--window-position=50,50",
    "--window-size=1280,800",
];

export function createBrowserSession(
    platform: PlatformProfile,
    config: TestConfig,
): BrowserSession {
    let browser: Browser | null = null;

    function requireBrowser(): Browser {
        if ( browser === null ) throw new Error( "The browser session is not open." );
        return browser;
    }

    async function present( page: Page ): Promise<Page> {
        if ( config.showBrowser ) await page.bringToFront();
        return page;
    }

    return {
        async open(): Promise<void> {
            browser = await chromium.launch({
                executablePath: platform.browserExecutablePath(),
                headless: !config.showBrowser,
                slowMo: config.slowMoMs,
                args: [
                    ...platform.browserLaunchArgs(),
                    ...( config.showBrowser ? HEADED_WINDOW_ARGS : [] ),
                ],
            });
        },

        async newPage(): Promise<ManagedPage> {
            const page = await present( await requireBrowser().newPage());
            return { page, dispose: () => page.close() };
        },

        async newInstrumentedPage( initScript: () => void ): Promise<ManagedPage> {
            const context = await requireBrowser().newContext();
            await context.addInitScript( initScript );
            const page = await present( await context.newPage());
            return { page, dispose: () => context.close() };
        },

        async close(): Promise<void> {
            await browser?.close();
            browser = null;
        },
    };
}
