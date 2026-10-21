/**
 * Port: raise the browser window so a human watching a headed run can see and
 * click the step dialog.
 */
export interface WindowFocuser {
    focus(): void;
}

/** Null Object. Headless runs and non-Windows hosts need no focus call. */
export function createNullWindowFocuser(): WindowFocuser {
    return {
        focus(): void {
            // Nothing to raise.
        },
    };
}
