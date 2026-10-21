import type { WindowFocuser } from "../window-focuser/window-focuser.js";

export interface ServerSpawnPlan {
    readonly command: string;
    readonly args: ReadonlyArray<string>;
    readonly cwd: string | null;
}

/**
 * Abstract Factory. One profile supplies the whole family of host-specific
 * products a run needs: how to launch `server.py`, which Chrome binary to
 * drive, and how to raise its window. Adding a host means adding a profile,
 * not another `IS_WINDOWS` branch.
 */
export interface PlatformProfile {
    readonly name: string;
    serverSpawnPlan( port: number ): ServerSpawnPlan;
    browserExecutablePath(): string;
    browserLaunchArgs(): ReadonlyArray<string>;
    createWindowFocuser(): WindowFocuser;
}
