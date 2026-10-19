/** Trace files belong to the playground; payment evidence belongs to finance. */
import { resolve } from 'node:path'

export function financeRoot(viewerDir: string, configured?: string): string {
  return configured ? resolve(configured) : resolve(viewerDir, '../../../rol_finances')
}
