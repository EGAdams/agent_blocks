/** Chain-of-Responsibility handler. Implementations must be idempotent. */
export interface ContentEnhancer {
    enhance( container: HTMLElement ): void | Promise<void>;
}
