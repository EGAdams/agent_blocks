import assert from "node:assert/strict";
import { createServer } from "node:net";

/** Port: hands out a TCP port the SPA server can bind. */
export interface PortReservation {
    reserve(): Promise<number>;
}

/** Binds port 0, reads what the kernel handed back, then releases it. */
export function createEphemeralPortReservation(): PortReservation {
    return {
        async reserve(): Promise<number> {
            const server = createServer();
            await new Promise<void>( ( resolve, reject ) => {
                server.once( "error", reject );
                server.listen( 0, "127.0.0.1", () => resolve());
            });
            const address = server.address();
            assert.ok( address && typeof address !== "string" );
            const { port } = address;
            await new Promise<void>( ( resolve, reject ) => {
                server.close( ( error ) => error ? reject( error ) : resolve());
            });
            return port;
        },
    };
}
