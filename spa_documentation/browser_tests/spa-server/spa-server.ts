import { spawn } from "node:child_process";
import type { ChildProcess } from "node:child_process";

import type { PlatformProfile } from "../platform-profile/platform-profile.js";
import type { PortReservation } from "../port-reservation/port-reservation.js";
import type { ServerReadiness } from "../server-readiness/server-readiness.js";

/**
 * Facade over "a `server.py` child process on a free port that answers HTTP".
 * Callers get a base URL and a way to stop it; they never see the child.
 */
export interface SpaServer {
    start(): Promise<string>;
    stop(): void;
}

export function createSpaServer(
    ports: PortReservation,
    platform: PlatformProfile,
    readiness: ServerReadiness,
): SpaServer {
    let child: ChildProcess | null = null;

    return {
        async start(): Promise<string> {
            const port = await ports.reserve();
            const plan = platform.serverSpawnPlan( port );
            child = plan.cwd === null
                ? spawn( plan.command, [ ...plan.args ], { stdio: "ignore" })
                : spawn( plan.command, [ ...plan.args ], { cwd: plan.cwd, stdio: "ignore" });
            const baseUrl = `http://127.0.0.1:${port}`;
            await readiness.waitUntilReady( `${baseUrl}/index.html` );
            return baseUrl;
        },

        stop(): void {
            child?.kill( "SIGTERM" );
            child = null;
        },
    };
}
