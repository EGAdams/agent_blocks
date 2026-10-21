import { spawnSync } from "node:child_process";

import type { WindowFocuser } from "../window-focuser/window-focuser.js";

/**
 * Raises native Windows Chrome from WSL. This is the only module allowed to
 * shell out to PowerShell.
 */
export function createPowerShellWindowFocuser( windowTitlePrefix: string ): WindowFocuser {
    const script = [
        "$chrome = Get-Process chrome |",
        `Where-Object { $_.MainWindowTitle -like '${windowTitlePrefix}*' } |`,
        "Select-Object -First 1;",
        "if ($chrome) {",
        "(New-Object -ComObject WScript.Shell).AppActivate($chrome.Id) | Out-Null",
        "}",
    ].join( " " );

    return {
        focus(): void {
            spawnSync( "powershell.exe", [ "-NoProfile", "-Command", script ], {
                stdio: "ignore",
            });
        },
    };
}
