/** DOM sink only. Content selection and network access live elsewhere. */
export interface ContentHost {
    element(): HTMLElement;
    replace( html: string ): Promise<void>;
    showPlaceholder( message: string ): Promise<void>;
}

export declare function createDomContentHost( element: HTMLElement ): ContentHost;
