import { expect, test } from 'bun:test'
import { Database } from 'bun:sqlite'
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { ReportRefresher, type StatusRunner } from './refresh'
import { interactiveReport } from './index'

function fixture() {
  const root = mkdtempSync(resolve(tmpdir(), 'refresh-report-'))
  const sessions = resolve(root, 'runtime/sessions'), finance = resolve(root, 'finance')
  mkdirSync(resolve(sessions, 'old'), { recursive: true })
  mkdirSync(resolve(finance, 'adws'), { recursive: true })
  writeFileSync(resolve(finance, 'adws/adw_tax_status.py'), '# test workflow')
  const oldReport = '<html><head></head><body><section id="expense-summary"><h2>Expense summary</h2><p>2 expenses</p></section></body></html>'
  writeFileSync(resolve(sessions, 'old/remaining-work.html'), oldReport)
  const dbPath = resolve(root, 'runtime/sssf.db')
  const db = new Database(dbPath)
  db.exec('CREATE TABLE sessions (adw_id TEXT, status TEXT)')
  return { root, sessions, finance, dbPath, db, oldReport }
}

test('refresh starts a new status run, deduplicates concurrent clicks, and preserves the old snapshot', async () => {
  const f = fixture()
  let calls = 0
  let release!: () => void
  const ready = new Promise<void>(resolve => { release = resolve })
  const runner: StatusRunner = async request => {
    calls++
    const cfg = JSON.parse(readFileSync(request.configPath, 'utf8'))
    expect(cfg.defaults.data_dir).toBe(resolve(f.sessions, '..'))
    expect(cfg.observability.db).toBe(f.dbPath)
    expect(cfg.agents).toEqual([])
    expect(request.financeRoot).toBe(f.finance)
    await ready
    f.db.query('INSERT INTO sessions VALUES (?, ?)').run(request.adwId, 'success')
    writeFileSync(resolve(f.sessions, request.adwId, 'remaining-work.html'), '<section id="expense-summary">1 expense</section>')
    return 0
  }
  try {
    const service = new ReportRefresher(f.finance, f.dbPath, f.sessions, runner)
    const first = service.refresh('old'), concurrent = service.refresh('old')
    expect(first).toBe(concurrent)
    release()
    const result = await first
    expect(calls).toBe(1)
    expect(result.adw_id).not.toBe('old')
    expect(result.report_url).toBe(`/api/sessions/${result.adw_id}/reports/remaining-work.html`)
    expect(readFileSync(resolve(f.sessions, 'old/remaining-work.html'), 'utf8')).toBe(f.oldReport)
    const next = await service.refresh(result.adw_id)
    expect(next.adw_id).not.toBe(result.adw_id)
    expect(calls).toBe(2)
  } finally { f.db.close(); rmSync(f.root, { recursive: true, force: true }) }
})

test('failed or incomplete checks never replace a report and can be retried', async () => {
  const f = fixture()
  let calls = 0
  const runner: StatusRunner = async request => {
    calls++
    f.db.query('INSERT INTO sessions VALUES (?, ?)').run(request.adwId, 'fail')
    writeFileSync(resolve(f.sessions, request.adwId, 'remaining-work.html'), 'Incomplete')
    return calls === 1 ? 1 : 0
  }
  try {
    const service = new ReportRefresher(f.finance, f.dbPath, f.sessions, runner)
    await expect(service.refresh('old')).rejects.toThrow('could not read all current sources')
    await expect(service.refresh('old')).rejects.toThrow('did not publish a complete report')
    await expect(service.refresh('../old')).rejects.toThrow('Invalid source session')
    expect(calls).toBe(2)
    expect(readFileSync(resolve(f.sessions, 'old/remaining-work.html'), 'utf8')).toBe(f.oldReport)
  } finally { f.db.close(); rmSync(f.root, { recursive: true, force: true }) }
})

test('old and new reports get exactly one refresh button and only the trusted external script', () => {
  const before = '<html><head></head><body><section id="expense-summary"><h2>Expense summary</h2></section></body></html>'
  const after = interactiveReport(before)
  expect(after).toContain('<h2>Expense summary <button type="button" id="refresh-totals">Refresh Totals</button>')
  expect(after).toContain('<script src="/api/reports/refresh.js" defer></script>')
  expect(after).toContain('role="status"')
  expect(after.split('id="refresh-totals"')).toHaveLength(2)
  expect(interactiveReport(after).split('id="refresh-totals"')).toHaveLength(2)
  expect(interactiveReport('<html>no expense data</html>')).toBe('<html>no expense data</html>')
})
