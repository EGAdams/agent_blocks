export interface ClipboardWriter {
    writeText( text: string ): Promise<void>;
}

export declare function createBrowserClipboardWriter(
    navigator: Navigator,
    document: Document,
): ClipboardWriter;
