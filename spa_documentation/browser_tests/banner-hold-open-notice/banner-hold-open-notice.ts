import type { Page } from "playwright-core";

import type { HoldOpenNotice } from "../hold-open-notice/hold-open-notice.js";
import type { WindowFocuser } from "../window-focuser/window-focuser.js";

interface BannerSpec {
    readonly holdOpenMs: number;
}

function installBanner( spec: BannerSpec ): void {
    const banner = document.createElement( "div" );
    banner.textContent =
        `Browser test run finished. Closing in ${spec.holdOpenMs / 1000} seconds…`;
    Object.assign( banner.style, {
        background: "#17365d",
        color: "white",
        font: "600 20px system-ui",
        left: "0",
        padding: "18px 24px",
        position: "fixed",
        right: "0",
        textAlign: "center",
        top: "0",
        zIndex: "99999",
    });
    document.body.appendChild( banner );
}

/** Headed runs: leave a visible result page up for a fixed hold. */
export function createBannerHoldOpenNotice(
    focuser: WindowFocuser,
    holdOpenMs: number,
): HoldOpenNotice {
    return {
        async present( openPage: () => Promise<Page> ): Promise<void> {
            const page = await openPage();
            await page.evaluate( installBanner, { holdOpenMs });
            focuser.focus();
            await new Promise( ( resolve ) => setTimeout( resolve, holdOpenMs ) );
        },
    };
}
