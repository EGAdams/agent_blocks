import { expect, test } from 'bun:test'
import { mkdtempSync, mkdirSync, writeFileSync, symlinkSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { remainingWorkResponse } from './index'

test('serves the saved session report and rejects missing, traversed, or symlinked files', async () => {
  const root = mkdtempSync(join(tmpdir(), 'tax-report-'))
  try {
    const sessions = join(root, 'sessions')
    const session = join(sessions, 'run')
    mkdirSync(session, { recursive: true })
    const html = '<!doctype html><title>Remaining work</title><h1>638 decisions</h1>'
    writeFileSync(join(session, 'remaining-work.html'), html)
    const response = remainingWorkResponse(sessions, 'run')
    expect(response.status).toBe(200)
    expect(await response.text()).toBe(html)
    expect(response.headers.get('content-type')).toBe('text/html; charset=utf-8')
    expect(response.headers.get('content-security-policy')).toContain("default-src 'none'")
    expect(response.headers.get('cache-control')).toBe('no-store')
    expect(remainingWorkResponse(sessions, 'missing').status).toBe(404)
    expect(remainingWorkResponse(sessions, '../run').status).toBe(400)
    expect(remainingWorkResponse(sessions, '..').status).toBe(400)
    const other = join(root, 'outside')
    mkdirSync(other)
    writeFileSync(join(other, 'remaining-work.html'), 'private')
    symlinkSync(other, join(sessions, 'escaped-session'))
    expect(remainingWorkResponse(sessions, 'escaped-session').status).toBe(403)
    const escapedFile = join(sessions, 'escaped-file')
    mkdirSync(escapedFile)
    symlinkSync(join(other, 'remaining-work.html'), join(escapedFile, 'remaining-work.html'))
    expect(remainingWorkResponse(sessions, 'escaped-file').status).toBe(403)
  } finally { rmSync(root, { recursive: true, force: true }) }
})
