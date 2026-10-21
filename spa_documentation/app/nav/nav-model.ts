export interface NavigationItem {
    readonly text: string;
    readonly active: boolean;
    readonly className: string | null;
    readonly select: () => void;
}

export interface NavigationModel {
    readonly items: ReadonlyArray<NavigationItem>;
}
