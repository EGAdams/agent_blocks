import { expect, test } from 'bun:test'
import { mkdtempSync, mkdirSync, writeFileSync, symlinkSync, rmSync, unlinkSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { expenseDocumentResponse } from './expenses'

test('report links open only the recorded expense document and preserve CSV text', async () => {
  const root = mkdtempSync(join(tmpdir(), 'expense-report-'))
  try {
    const sessions = join(root, 'sessions'), run = join(sessions, 'run')
    const documents = join(root, 'readable_documents/scanned_statements')
    mkdirSync(run, { recursive: true }); mkdirSync(documents, { recursive: true })
    const receipt = join(documents, 'bill.pdf'), csv = join(documents, 'statement.csv')
    writeFileSync(receipt, '%PDF-1.4\nreceipt')
    writeFileSync(csv, 'date,amount\n2025-01-01,10')
    writeFileSync(join(run, 'remaining-expenses.json'), JSON.stringify([
      { expense: { id: 123 }, documents: [{ path: receipt }, { path: csv },
        { path: 'https://example.com/order' }, { path: '' }] },
    ]))
    const get = (id: string, index: string) => expenseDocumentResponse(sessions, root, 'run', id, index)
    const pdf = get('123', '0')
    expect(pdf.status).toBe(200)
    expect(pdf.headers.get('content-type')).toBe('application/pdf')
    expect(await pdf.text()).toStartWith('%PDF')
    // The viewer needs a scripting origin to render; framing stays shut both ways.
    const csp = pdf.headers.get('content-security-policy')!
    expect(csp).toContain('sandbox allow-scripts allow-same-origin')
    expect(csp).toContain("frame-ancestors 'self'")
    expect(csp).toContain("default-src 'none'")
    expect(get('123', '1').headers.get('content-type')).toStartWith('text/plain')
    expect(get('123', '1').headers.get('cache-control')).toBe('no-store')
    expect(get('123', '2').status).toBe(302)
    expect(get('123', '2').headers.get('location')).toBe('https://example.com/order')
    expect(get('124', '0').status).toBe(404)
    expect(get('123', '3').status).toBe(404)
    expect(get('123', '99').status).toBe(404)
    expect(get('123', '../0').status).toBe(400)
    unlinkSync(receipt)
    expect(get('123', '0').status).toBe(404)
  } finally { rmSync(root, { recursive: true, force: true }) }
})

test('report evidence blocks symlinks, credentials and active documents', () => {
  const root = mkdtempSync(join(tmpdir(), 'expense-report-'))
  try {
    const sessions = join(root, 'sessions'), run = join(sessions, 'run')
    const documents = join(root, 'readable_documents/receipts')
    mkdirSync(run, { recursive: true }); mkdirSync(documents, { recursive: true })
    const privateFile = join(root, 'private.pdf'), link = join(documents, 'escape.pdf')
    const html = join(documents, 'active.html')
    writeFileSync(privateFile, 'private'); symlinkSync(privateFile, link)
    writeFileSync(html, '<script>active()</script>')
    const manifest = join(run, 'remaining-expenses.json')
    writeFileSync(manifest, JSON.stringify([{ expense: { id: 1 }, documents: [
      { path: link }, { path: 'javascript:alert(1)' }, { path: 'https://user:secret@example.com' }, { path: html },
    ] }]))
    const get = (index: string) => expenseDocumentResponse(sessions, root, 'run', '1', index)
    expect(get('0').status).toBe(403)
    expect(get('1').status).toBe(400)
    expect(get('2').status).toBe(400)
    expect(get('3').headers.get('content-disposition')).toStartWith('attachment; filename*=')
    // An unknown type downloads, so it keeps the strict opaque-origin sandbox.
    expect(get('3').headers.get('content-security-policy')).toEndWith('sandbox')
    expect(expenseDocumentResponse(sessions, root, '../run', '1', '0').status).toBe(400)
    symlinkSync(run, join(sessions, 'escaped'))
    expect(expenseDocumentResponse(sessions, root, 'escaped', '1', '0').status).toBe(403)
    unlinkSync(manifest); symlinkSync(privateFile, manifest)
    expect(get('0').status).toBe(403)
  } finally { rmSync(root, { recursive: true, force: true }) }
})
