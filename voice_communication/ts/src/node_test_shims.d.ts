/** Minimal ambient types for node:test/node:assert so tests typecheck without @types/node. */

declare module "node:test" {
  export function test( name: string, fn: () => void | Promise<void> ): void;
}

declare module "node:assert/strict" {
  interface StrictAssert {
    ( value: unknown, message?: string ): asserts value;
    equal( actual: unknown, expected: unknown, message?: string ): void;
    notEqual( actual: unknown, expected: unknown, message?: string ): void;
    deepEqual( actual: unknown, expected: unknown, message?: string ): void;
    ok( value: unknown, message?: string ): asserts value;
    throws( fn: () => void, message?: string ): void;
    rejects( fn: () => Promise<unknown>, message?: string ): Promise<void>;
  }

  const assert: StrictAssert;
  export default assert;
}
