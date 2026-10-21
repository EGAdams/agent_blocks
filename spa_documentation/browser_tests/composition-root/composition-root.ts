import { createBannerHoldOpenNotice } from "../banner-hold-open-notice/banner-hold-open-notice.js";
import { createBrowserSession } from "../browser-session/browser-session.js";
import { createDialogStepGate } from "../dialog-step-gate/dialog-step-gate.js";
import type { HoldOpenNotice } from "../hold-open-notice/hold-open-notice.js";
import { createNullHoldOpenNotice } from "../hold-open-notice/hold-open-notice.js";
import { createLinuxPlatformProfile } from "../linux-platform-profile/linux-platform-profile.js";
import { createNavigationProbe } from "../nav-probe/nav-probe.js";
import type { PlatformProfile } from "../platform-profile/platform-profile.js";
import { createEphemeralPortReservation } from "../port-reservation/port-reservation.js";
import { createHttpPollingReadiness } from "../server-readiness/server-readiness.js";
import { createSpaServer } from "../spa-server/spa-server.js";
import type { StepGate } from "../step-gate/step-gate.js";
import { createNullStepGate } from "../step-gate/step-gate.js";
import type { TestConfig } from "../test-config/test-config.js";
import { readTestConfig } from "../test-config/test-config.js";
import type { TestEnvironment } from "../test-environment/test-environment.js";
import { createTestEnvironment } from "../test-environment/test-environment.js";
import { createWindowsPlatformProfile } from "../windows-platform-profile/windows-platform-profile.js";
import type { WindowFocuser } from "../window-focuser/window-focuser.js";
import { createNullWindowFocuser } from "../window-focuser/window-focuser.js";

const READINESS_SCHEDULE = { attempts: 50, intervalMs: 100 };

function selectPlatformProfile( config: TestConfig ): PlatformProfile {
    return config.isWindows
        ? createWindowsPlatformProfile( config )
        : createLinuxPlatformProfile( config );
}

function selectWindowFocuser( config: TestConfig, platform: PlatformProfile ): WindowFocuser {
    return config.showBrowser ? platform.createWindowFocuser() : createNullWindowFocuser();
}

function selectStepGate( config: TestConfig, focuser: WindowFocuser ): StepGate {
    return config.showBrowser ? createDialogStepGate( focuser ) : createNullStepGate();
}

function selectHoldOpenNotice( config: TestConfig, focuser: WindowFocuser ): HoldOpenNotice {
    return config.showBrowser && config.holdOpenMs > 0
        ? createBannerHoldOpenNotice( focuser, config.holdOpenMs )
        : createNullHoldOpenNotice();
}

/**
 * The only module that names concrete implementations. Every host difference
 * and every headed/headless difference is resolved here, once.
 */
export function createLegacyNavigationEnvironment(): TestEnvironment {
    const config = readTestConfig({
        platform: process.platform,
        env: process.env,
        moduleUrl: import.meta.url,
    });
    const platform = selectPlatformProfile( config );
    const focuser = selectWindowFocuser( config, platform );

    return createTestEnvironment({
        server: createSpaServer(
            createEphemeralPortReservation(),
            platform,
            createHttpPollingReadiness( READINESS_SCHEDULE ),
        ),
        browser: createBrowserSession( platform, config ),
        probe: createNavigationProbe(),
        gate: selectStepGate( config, focuser ),
        holdOpen: selectHoldOpenNotice( config, focuser ),
    });
}
