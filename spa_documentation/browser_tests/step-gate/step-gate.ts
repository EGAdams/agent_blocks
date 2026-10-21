import type { Page } from "playwright-core";

/**
 * Port: pause between test steps until a human acknowledges them.
 */
export interface StepGate {
    waitForAcknowledgement( page: Page ): Promise<void>;
}

/** Null Object. A headless run never pauses, so there is no flag to poll. */
export function createNullStepGate(): StepGate {
    return {
        async waitForAcknowledgement(): Promise<void> {
            // Headless runs continue immediately.
        },
    };
}
