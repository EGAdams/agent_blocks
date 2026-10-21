import type { Page } from "playwright-core";

import type { OkDialogSpec } from "../dialog-builder/dialog-builder.js";
import { installOkDialog } from "../dialog-builder/dialog-builder.js";
import type { StepGate } from "../step-gate/step-gate.js";
import type { WindowFocuser } from "../window-focuser/window-focuser.js";

export const OK_DIALOG_SPEC: OkDialogSpec = {
    dialogId: "spa-test-ok-dialog",
    okButtonId: "spa-test-ok",
    flagName: "__spaTestOkClicked",
    title: "Agent Blocks Docs",
    message: "Test step finished. Click OK to continue.",
};

/**
 * Headed runs: build the dialog in the page, raise the window, then block on
 * the click flag with no timeout so the watcher sets the pace.
 */
export function createDialogStepGate(
    focuser: WindowFocuser,
    spec: OkDialogSpec = OK_DIALOG_SPEC,
): StepGate {
    return {
        async waitForAcknowledgement( page: Page ): Promise<void> {
            await page.evaluate( installOkDialog, spec );
            focuser.focus();
            await page.waitForFunction(
                ( flagName: string ) =>
                    ( window as unknown as Record<string, unknown> )[ flagName ] === true,
                spec.flagName,
                { polling: 100, timeout: 0 },
            );
        },
    };
}
