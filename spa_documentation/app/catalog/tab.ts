export type DetailTabKey =
    | "source"
    | "class"
    | "sequence"
    | "status"
    | "update_docs";

export interface DetailTabDescriptor {
    readonly key: DetailTabKey;
    readonly label: string;
    readonly file: string;
}
