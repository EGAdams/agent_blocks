import { expect, test } from 'bun:test'
import { mkdtempSync, mkdirSync, writeFileSync, symlinkSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { pdfResponse, receiptResponse, resolveReceipt } from './index'
import { financeRoot } from '../paths'

test('canonical finance resolver reports a missing stored filename', async () => {
  const root = financeRoot(resolve(import.meta.dir, '../..'), process.env.ROL_FINANCE_ROOT)
  expect(await resolveReceipt('adw-test-nonexistent-receipt-6b0197cc.jpg', root)).toBeNull()
})

test('serves only the selected receipt bytes for inline display', async () => {
  const root = mkdtempSync(join(tmpdir(), 'tax-receipt-'))
  try {
    const folder = join(root, 'readable_documents/receipts')
    mkdirSync(folder, { recursive: true })
    const path = join(folder, 'bill.jpg')
    const bytes = new Uint8Array([255, 216, 255, 217])
    writeFileSync(path, bytes)
    const response = await receiptResponse('bill.jpg', root, async stored => {
      expect(stored).toBe('bill.jpg')
      return path
    })
    expect(response.status).toBe(200)
    expect(response.headers.get('content-type')).toBe('image/jpeg')
    expect(response.headers.get('cache-control')).toBe('no-store')
    expect(new Uint8Array(await response.arrayBuffer())).toEqual(bytes)
  } finally { rmSync(root, { recursive: true, force: true }) }
})

test('missing receipts and remote links do not perform unintended lookups', async () => {
  const forbidden = async (): Promise<null> => { throw new Error('Unexpected lookup') }
  expect((await receiptResponse('', '/tmp', forbidden)).status).toBe(404)
  expect((await receiptResponse('javascript:alert(1)', '/tmp', forbidden)).status).toBe(400)
  expect((await receiptResponse('https://user:password@example.com', '/tmp', forbidden)).status).toBe(400)
  const remote = await receiptResponse('https://example.com/receipt?order=123', '/tmp', forbidden)
  expect(remote.status).toBe(302)
  expect(remote.headers.get('location')).toBe('https://example.com/receipt?order=123')
  expect((await receiptResponse('missing.jpg', '/tmp', async () => null)).status).toBe(404)
})

test('rejects paths outside receipt folders, symlink escapes, and active file types', async () => {
  const root = mkdtempSync(join(tmpdir(), 'tax-receipt-'))
  try {
    const receipts = join(root, 'readable_documents/receipts')
    mkdirSync(receipts, { recursive: true })
    const outside = join(root, 'private.jpg')
    writeFileSync(outside, 'private')
    const link = join(receipts, 'escape.jpg')
    symlinkSync(outside, link)
    const html = join(receipts, 'active.html')
    writeFileSync(html, '<script>alert(1)</script>')
    expect((await receiptResponse('../private.jpg', root, async () => outside)).status).toBe(403)
    expect((await receiptResponse('escape.jpg', root, async () => link)).status).toBe(403)
    expect((await receiptResponse('active.html', root, async () => html)).status).toBe(415)
  } finally { rmSync(root, { recursive: true, force: true }) }
})

test('PDF viewer serves statement bytes but rejects non-PDFs and symlink escapes', async () => {
  const root = mkdtempSync(join(tmpdir(), 'tax-pdf-'))
  try {
    const folder = join(root, 'readable_documents/scanned_statements/2025')
    mkdirSync(folder, { recursive: true })
    const path = join(folder, 'august.pdf')
    const bytes = new TextEncoder().encode('%PDF-1.4\nstatement\n%%EOF')
    writeFileSync(path, bytes)
    const response = await pdfResponse(path, root, async () => path)
    expect(response.status).toBe(200)
    expect(response.headers.get('content-type')).toBe('application/pdf')
    expect(response.headers.get('content-disposition')).toBe('inline')
    expect(response.headers.get('cache-control')).toBe('no-store')
    expect(new Uint8Array(await response.arrayBuffer())).toEqual(bytes)
    expect((await receiptResponse(path, root, async () => path)).status).toBe(403)
    const image = join(folder, 'not-pdf.jpg')
    writeFileSync(image, 'image')
    expect((await pdfResponse(image, root, async () => image)).status).toBe(415)
    const outside = join(root, 'private.pdf')
    writeFileSync(outside, 'private')
    const escape = join(folder, 'escape.pdf')
    symlinkSync(outside, escape)
    expect((await pdfResponse(escape, root, async () => escape)).status).toBe(403)
    expect((await pdfResponse('', root)).status).toBe(404)
  } finally { rmSync(root, { recursive: true, force: true }) }
})
