import type { PlatformProfile, ServerSpawnPlan } from "../platform-profile/platform-profile.js";
import type { TestConfig } from "../test-config/test-config.js";
import type { WindowFocuser } from "../window-focuser/window-focuser.js";
import { createNullWindowFocuser } from "../window-focuser/window-focuser.js";

const CHROME_PATH = "/usr/bin/google-chrome";

/** Linux host: `server.py` and Chrome both run in-place. */
export function createLinuxPlatformProfile( config: TestConfig ): PlatformProfile {
    return {
        name: "linux",

        serverSpawnPlan( port: number ): ServerSpawnPlan {
            return {
                command: "python3",
                args: [ "server.py", String( port ) ],
                cwd: config.projectRoot,
            };
        },

        browserExecutablePath(): string {
            return CHROME_PATH;
        },

        browserLaunchArgs(): ReadonlyArray<string> {
            return [ "--no-sandbox" ];
        },

        createWindowFocuser(): WindowFocuser {
            return createNullWindowFocuser();
        },
    };
}
