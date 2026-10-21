/** Port: block until a URL answers, or give up. */
export interface ServerReadiness {
    waitUntilReady( url: string ): Promise<void>;
}

export interface ReadinessSchedule {
    readonly attempts: number;
    readonly intervalMs: number;
}

export function createHttpPollingReadiness( schedule: ReadinessSchedule ): ServerReadiness {
    return {
        async waitUntilReady( url: string ): Promise<void> {
            for ( let attempt = 0; attempt < schedule.attempts; attempt += 1 ) {
                try {
                    const response = await fetch( url );
                    if ( response.ok ) return;
                } catch {
                    // The child process may still be binding its socket.
                }
                await new Promise( ( resolve ) => setTimeout( resolve, schedule.intervalMs ) );
            }
            throw new Error( `SPA server did not become ready at ${url}` );
        },
    };
}
