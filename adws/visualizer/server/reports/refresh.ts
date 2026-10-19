/** Reuse the read-only status ADW; each refresh publishes a new saved snapshot. */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { Database } from 'bun:sqlite'
import { remainingWorkUrl } from '../../shared/report-links'

export interface StatusRunRequest {
  financeRoot: string
  adwId: string
  configPath: string
}
export type StatusRunner = (request: StatusRunRequest) => Promise<number>
export interface RefreshedReport { adw_id: string; report_url: string }

const runStatus: StatusRunner = async request => {
  const child = Bun.spawn(['uv', 'run', resolve(request.financeRoot, 'adws/adw_tax_status.py'),
    '--config', request.configPath, '--finance-root', request.financeRoot, '--adw-id', request.adwId],
  { cwd: request.financeRoot, stdin: 'ignore', stdout: 'pipe', stderr: 'pipe' })
  const timer = setTimeout(() => child.kill(), 60000)
  try {
    const [, , code] = await Promise.all([
      new Response(child.stdout).text(), new Response(child.stderr).text(), child.exited,
    ])
    return code
  } finally { clearTimeout(timer) }
}

export class ReportRefresher {
  private pending = new Map<string, Promise<RefreshedReport>>()

  constructor(private financeRoot: string, private dbPath: string,
      private sessionsDir: string, private runner: StatusRunner = runStatus) {}

  refresh(sourceId: string): Promise<RefreshedReport> {
    if (!remainingWorkUrl(sourceId)) return Promise.reject(new Error('Invalid source session.'))
    const running = this.pending.get(sourceId)
    if (running) return running
    const promise = this.execute().finally(() => { this.pending.delete(sourceId) })
    this.pending.set(sourceId, promise)
    return promise
  }

  private async execute(): Promise<RefreshedReport> {
    const adwId = crypto.randomUUID().replaceAll('-', '').slice(0, 8)
    const directory = resolve(this.sessionsDir, adwId)
    if (!existsSync(resolve(this.financeRoot, 'adws/adw_tax_status.py'))) {
      throw new Error('The finance tax-status workflow is unavailable. Check the viewer’s finance checkout setting.')
    }
    mkdirSync(directory, { recursive: true })
    const configPath = resolve(directory, 'refresh-config.json')
    writeFileSync(configPath, JSON.stringify({ defaults: { data_dir: resolve(this.sessionsDir, '..') },
      observability: { db: this.dbPath }, agents: [] }))
    const code = await this.runner({ financeRoot: this.financeRoot, adwId, configPath })
    if (code !== 0) throw new Error(`The status check could not read all current sources. See run ${adwId} in the viewer.`)
    const db = new Database(this.dbPath, { readonly: true })
    try {
      const session = db.query<{ status: string }, [string]>(
        'SELECT status FROM sessions WHERE adw_id = ?').get(adwId)
      const report = resolve(directory, 'remaining-work.html')
      if (session?.status !== 'success' || !existsSync(report)
          || !readFileSync(report, 'utf8').includes('id="expense-summary"')) {
        throw new Error(`The status check did not publish a complete report. See run ${adwId} in the viewer.`)
      }
    } finally { db.close() }
    return { adw_id: adwId, report_url: remainingWorkUrl(adwId)! }
  }
}
