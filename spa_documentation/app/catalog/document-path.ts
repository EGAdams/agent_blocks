declare const itemKeyBrand: unique symbol;

export type ItemKey = string & { readonly [ itemKeyBrand ]: "ItemKey" };

export interface DocumentPathResolver {
    itemKey( top: string, path: ReadonlyArray<string> ): ItemKey;
    fragmentPath( itemKey: ItemKey, fileName: string ): string;
}

export declare function createDocumentPathResolver(): DocumentPathResolver;
