/** Minimal ambient types for node:fs/promises and node:url, since no @types/node is vendored. */

declare module "node:fs/promises" {
  function readdir( path: string, options?: { recursive?: boolean } ): Promise<string[]>;
  function readFile( path: string, encoding: string ): Promise<string>;
}

declare module "node:url" {
  function fileURLToPath( url: URL | string ): string;
}

declare const process: { cwd(): string };
