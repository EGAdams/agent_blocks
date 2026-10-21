import type { Page } from "playwright-core";

/**
 * Port: what happens after the last test, before the browser closes.
 *
 * The page is supplied lazily so a run that holds nothing open never pays for
 * opening one.
 */
export interface HoldOpenNotice {
    present( openPage: () => Promise<Page> ): Promise<void>;
}

/** Null Object. Headless runs close as soon as the suite ends. */
export function createNullHoldOpenNotice(): HoldOpenNotice {
    return {
        async present(): Promise<void> {
            // Nothing to show and nothing to wait for.
        },
    };
}
