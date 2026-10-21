export interface OkDialogSpec {
    readonly dialogId: string;
    readonly okButtonId: string;
    readonly flagName: string;
    readonly title: string;
    readonly message: string;
}

/**
 * Builder for the Windows 95 style step dialog.
 *
 * This function is serialized and evaluated inside the page, so it must stay
 * entirely self-contained: every part builder is declared in its own body and
 * it closes over nothing but `spec`.
 */
export function installOkDialog( spec: OkDialogSpec ): void {
    const flags = window as unknown as Record<string, unknown>;
    flags[ spec.flagName ] = false;

    const buildFrame = (): HTMLDivElement => {
        const frame = document.createElement( "div" );
        frame.id = spec.dialogId;
        Object.assign( frame.style, {
            background: "#c0c0c0",
            borderColor: "#dfdfdf #000000 #000000 #dfdfdf",
            borderStyle: "solid",
            borderWidth: "2px",
            boxShadow: "inset 1px 1px 0 #ffffff, inset -1px -1px 0 #808080",
            font: "11px Tahoma, 'MS Sans Serif', sans-serif",
            left: "calc( 50% - 150px )",
            padding: "2px",
            position: "fixed",
            top: "120px",
            userSelect: "none",
            width: "300px",
            zIndex: "99999",
        });
        return frame;
    };

    const buildCloseButton = (): HTMLButtonElement => {
        const closeButton = document.createElement( "button" );
        closeButton.textContent = "✕";
        Object.assign( closeButton.style, {
            background: "#c0c0c0",
            borderColor: "#ffffff #000000 #000000 #ffffff",
            borderStyle: "solid",
            borderWidth: "2px",
            color: "#000000",
            cursor: "default",
            font: "bold 9px Tahoma, 'MS Sans Serif', sans-serif",
            height: "16px",
            lineHeight: "1",
            padding: "0",
            width: "18px",
        });
        return closeButton;
    };

    const buildTitleBar = ( closeButton: HTMLButtonElement ): HTMLDivElement => {
        const titleBar = document.createElement( "div" );
        Object.assign( titleBar.style, {
            alignItems: "center",
            background: "linear-gradient( 90deg, #000080, #1084d0 )",
            color: "#ffffff",
            cursor: "move",
            display: "flex",
            font: "bold 11px Tahoma, 'MS Sans Serif', sans-serif",
            justifyContent: "space-between",
            padding: "3px 3px 3px 4px",
        });
        const titleText = document.createElement( "span" );
        titleText.textContent = spec.title;
        titleBar.append( titleText, closeButton );
        return titleBar;
    };

    const buildBody = (): HTMLDivElement => {
        const body = document.createElement( "div" );
        Object.assign( body.style, {
            alignItems: "center",
            display: "flex",
            gap: "14px",
            padding: "18px 14px 10px",
        });
        const icon = document.createElement( "div" );
        icon.textContent = "i";
        Object.assign( icon.style, {
            alignItems: "center",
            background: "#000080",
            borderRadius: "50%",
            color: "#ffffff",
            display: "flex",
            flex: "0 0 auto",
            font: "italic bold 20px Times New Roman, serif",
            height: "32px",
            justifyContent: "center",
            width: "32px",
        });
        const message = document.createElement( "div" );
        message.textContent = spec.message;
        body.append( icon, message );
        return body;
    };

    const bevel = ( button: HTMLButtonElement, pressed: boolean ): void => {
        button.style.borderColor = pressed
            ? "#808080 #ffffff #ffffff #808080"
            : "#ffffff #000000 #000000 #ffffff";
        button.style.boxShadow = pressed
            ? "inset 1px 1px 0 #000000"
            : "inset 1px 1px 0 #dfdfdf, inset -1px -1px 0 #808080";
    };

    const buildOkButton = (): HTMLButtonElement => {
        const okButton = document.createElement( "button" );
        okButton.id = spec.okButtonId;
        okButton.textContent = "OK";
        Object.assign( okButton.style, {
            background: "#c0c0c0",
            borderStyle: "solid",
            borderWidth: "2px",
            color: "#000000",
            cursor: "default",
            font: "11px Tahoma, 'MS Sans Serif', sans-serif",
            height: "23px",
            outline: "1px dotted #000000",
            outlineOffset: "-4px",
            width: "75px",
        });
        bevel( okButton, false );
        okButton.addEventListener( "mousedown", () => bevel( okButton, true ) );
        okButton.addEventListener( "mouseup", () => bevel( okButton, false ) );
        okButton.addEventListener( "click", () => {
            flags[ spec.flagName ] = true;
        });
        return okButton;
    };

    const buildFooter = ( okButton: HTMLButtonElement ): HTMLDivElement => {
        const footer = document.createElement( "div" );
        Object.assign( footer.style, {
            display: "flex",
            justifyContent: "center",
            padding: "6px 0 14px",
        });
        footer.append( okButton );
        return footer;
    };

    const makeDraggable = (
        frame: HTMLDivElement,
        titleBar: HTMLDivElement,
        closeButton: HTMLButtonElement,
    ): void => {
        let dragOffsetX = 0;
        let dragOffsetY = 0;
        const onMove = ( event: MouseEvent ): void => {
            const maxLeft = window.innerWidth - frame.offsetWidth;
            const maxTop = window.innerHeight - frame.offsetHeight;
            const left = Math.min( Math.max( event.clientX - dragOffsetX, 0 ), maxLeft );
            const top = Math.min( Math.max( event.clientY - dragOffsetY, 0 ), maxTop );
            frame.style.left = `${left}px`;
            frame.style.top = `${top}px`;
        };
        const onUp = (): void => {
            document.removeEventListener( "mousemove", onMove );
            document.removeEventListener( "mouseup", onUp );
        };
        titleBar.addEventListener( "mousedown", ( event: MouseEvent ) => {
            if ( event.target === closeButton ) return;
            const bounds = frame.getBoundingClientRect();
            dragOffsetX = event.clientX - bounds.left;
            dragOffsetY = event.clientY - bounds.top;
            frame.style.left = `${bounds.left}px`;
            frame.style.top = `${bounds.top}px`;
            document.addEventListener( "mousemove", onMove );
            document.addEventListener( "mouseup", onUp );
            event.preventDefault();
        });
    };

    const frame = buildFrame();
    const closeButton = buildCloseButton();
    const titleBar = buildTitleBar( closeButton );
    const okButton = buildOkButton();
    closeButton.addEventListener( "click", () => okButton.click() );
    makeDraggable( frame, titleBar, closeButton );

    frame.append( titleBar, buildBody(), buildFooter( okButton ) );
    document.body.appendChild( frame );
    okButton.focus();
}
