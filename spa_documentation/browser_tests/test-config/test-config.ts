import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

/**
 * Immutable run configuration. Reading `process.env` and `process.platform`
 * happens here and nowhere else, so every other module is testable with a
 * hand-built config object.
 */
export interface TestConfig {
    readonly projectRoot: string;
    readonly linuxProjectRoot: string;
    readonly isWindows: boolean;
    readonly showBrowser: boolean;
    readonly slowMoMs: number;
    readonly holdOpenMs: number;
    readonly windowTitle: string;
}

export interface ConfigSource {
    readonly platform: string;
    readonly env: Readonly<Record<string, string | undefined>>;
    readonly moduleUrl: string;
}

const DEFAULT_LINUX_PROJECT_ROOT = "/home/adamsl/agent_blocks/spa_documentation";
const WINDOW_TITLE = "Agent Blocks Docs";

function readInteger( raw: string | undefined, fallback: number ): number {
    const parsed = Number.parseInt( raw ?? "", 10 );
    return Number.isFinite( parsed ) ? parsed : fallback;
}

/**
 * Walks up from the compiled module until it finds the package manifest, so
 * the same code works from `browser_tests/` sources and from `dist/`.
 */
function findProjectRoot( moduleUrl: string ): string {
    let directory = dirname( fileURLToPath( moduleUrl ) );
    while ( !existsSync( join( directory, "package.json" ) ) ) {
        const parent = dirname( directory );
        if ( parent === directory ) {
            throw new Error( "Could not locate the SPA project root from " + moduleUrl );
        }
        directory = parent;
    }
    return directory;
}

export function readTestConfig( source: ConfigSource ): TestConfig {
    const { env } = source;
    return {
        projectRoot: findProjectRoot( source.moduleUrl ),
        linuxProjectRoot: env[ "SPA_TEST_LINUX_ROOT" ] ?? DEFAULT_LINUX_PROJECT_ROOT,
        isWindows: source.platform === "win32",
        showBrowser: env[ "SPA_TEST_HEADED" ] === "1",
        slowMoMs: readInteger( env[ "SPA_TEST_SLOW_MO" ], 0 ),
        holdOpenMs: readInteger( env[ "SPA_TEST_HOLD_OPEN_MS" ], 0 ),
        windowTitle: WINDOW_TITLE,
    };
}
