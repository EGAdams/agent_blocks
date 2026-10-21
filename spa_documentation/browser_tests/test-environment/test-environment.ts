import type { BrowserSession, ManagedPage } from "../browser-session/browser-session.js";
import type { HoldOpenNotice } from "../hold-open-notice/hold-open-notice.js";
import type { NavigationProbe } from "../nav-probe/nav-probe.js";
import type { SpaPage } from "../spa-page/spa-page.js";
import { createSpaPage } from "../spa-page/spa-page.js";
import type { SpaServer } from "../spa-server/spa-server.js";
import type { StepGate } from "../step-gate/step-gate.js";

/**
 * Facade over the whole run: one `start()` brings up the server and the
 * browser, one `stop()` tears both down, and pages arrive pre-wired as
 * `SpaPage` objects.
 */
export interface TestEnvironment {
    start(): Promise<void>;
    stop(): Promise<void>;
    newPage(): Promise<SpaPage>;
    /** A page whose context runs `initScript` before any SPA code loads. */
    newInstrumentedPage( initScript: () => void ): Promise<SpaPage>;
}

export interface TestEnvironmentDependencies {
    readonly server: SpaServer;
    readonly browser: BrowserSession;
    readonly probe: NavigationProbe;
    readonly gate: StepGate;
    readonly holdOpen: HoldOpenNotice;
}

export function createTestEnvironment(
    dependencies: TestEnvironmentDependencies,
): TestEnvironment {
    const { server, browser, probe, gate, holdOpen } = dependencies;
    let baseUrl: string | null = null;

    function requireBaseUrl(): string {
        if ( baseUrl === null ) throw new Error( "The test environment is not started." );
        return baseUrl;
    }

    function wrap( managed: ManagedPage ): SpaPage {
        return createSpaPage({
            page: managed.page,
            baseUrl: requireBaseUrl(),
            probe,
            gate,
            dispose: () => managed.dispose(),
        });
    }

    return {
        async start(): Promise<void> {
            baseUrl = await server.start();
            await browser.open();
        },

        async stop(): Promise<void> {
            await holdOpen.present( async () => {
                const managed = await browser.newPage();
                await managed.page.goto( `${requireBaseUrl()}/index.html` );
                return managed.page;
            });
            await browser.close();
            server.stop();
            baseUrl = null;
        },

        async newPage(): Promise<SpaPage> {
            return wrap( await browser.newPage());
        },

        async newInstrumentedPage( initScript: () => void ): Promise<SpaPage> {
            return wrap( await browser.newInstrumentedPage( initScript ) );
        },
    };
}
