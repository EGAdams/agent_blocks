import type { PlatformProfile, ServerSpawnPlan } from "../platform-profile/platform-profile.js";
import type { TestConfig } from "../test-config/test-config.js";
import type { WindowFocuser } from "../window-focuser/window-focuser.js";
import { createPowerShellWindowFocuser } from "../powershell-window-focuser/powershell-window-focuser.js";

const WSL_DISTRIBUTION = "Ubuntu-26.04";
const CHROME_PATH = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";

/**
 * Windows host: Node runs natively, `server.py` runs inside WSL, and Chrome is
 * the desktop install so the headed run appears on the user's screen.
 */
export function createWindowsPlatformProfile( config: TestConfig ): PlatformProfile {
    return {
        name: "windows",

        serverSpawnPlan( port: number ): ServerSpawnPlan {
            return {
                command: "wsl.exe",
                args: [
                    "-d",
                    WSL_DISTRIBUTION,
                    "--cd",
                    config.linuxProjectRoot,
                    "python3",
                    "server.py",
                    String( port ),
                ],
                cwd: null,
            };
        },

        browserExecutablePath(): string {
            return CHROME_PATH;
        },

        browserLaunchArgs(): ReadonlyArray<string> {
            return [ "--no-sandbox" ];
        },

        createWindowFocuser(): WindowFocuser {
            return createPowerShellWindowFocuser( config.windowTitle );
        },
    };
}
