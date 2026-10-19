/** Read-only receipt display. Resolve stored names with the finance app's resolver. */
import { existsSync, realpathSync } from 'node:fs'
import { extname, isAbsolute, relative, resolve } from 'node:path'

export type ReceiptResolver = (stored: string, financeRoot: string) => Promise<string | null>

const resolveScript = `import json,sys
from expense_review.receipt_opener import resolve_path
path = resolve_path(sys.argv[1])
print(json.dumps(str(path) if path else None))`

export const resolveReceipt: ReceiptResolver = async (stored, financeRoot) => {
  const process = Bun.spawn(['python3', '-c', resolveScript, stored], {
    cwd: financeRoot, stdout: 'pipe', stderr: 'pipe',
  })
  const timer = setTimeout(() => process.kill(), 5000)
  try {
    const [output, , code] = await Promise.all([
      new Response(process.stdout).text(), new Response(process.stderr).text(), process.exited,
    ])
    if (code !== 0) throw new Error('Receipt lookup failed. Try Show receipt in the terminal.')
    const path: unknown = JSON.parse(output)
    if (path !== null && typeof path !== 'string') throw new Error('Invalid receipt lookup result')
    return path
  } finally { clearTimeout(timer) }
}

const mimeTypes: Record<string, string> = {
  '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
  '.gif': 'image/gif', '.webp': 'image/webp', '.pdf': 'application/pdf',
}

export async function receiptResponse(stored: string, financeRoot: string,
    lookup: ReceiptResolver = resolveReceipt): Promise<Response> {
  return documentResponse(stored, financeRoot, lookup, false)
}

export async function pdfResponse(stored: string, financeRoot: string,
    lookup: ReceiptResolver = resolveReceipt): Promise<Response> {
  return documentResponse(stored, financeRoot, lookup, true)
}

async function documentResponse(stored: string, financeRoot: string,
    lookup: ReceiptResolver, pdf: boolean): Promise<Response> {
  const headers = { 'cache-control': 'no-store', 'x-content-type-options': 'nosniff' }
  const label = pdf ? 'PDF' : 'Receipt'
  if (!stored.trim()) return new Response(`No ${label.toLowerCase()} is linked to this question.`, { status: 404, headers })
  // Remote order confirmations are opened by the browser, never fetched by the server.
  if (/^https?:\/\//i.test(stored)) {
    const url = new URL(stored)
    if (url.username || url.password) return new Response('Invalid receipt link.', { status: 400, headers })
    return new Response(null, { status: 302, headers: { ...headers, location: url.href } })
  }
  if (/^[a-z][a-z\d+.-]*:/i.test(stored)) return new Response('Unsupported receipt link.', { status: 400, headers })
  const found = await lookup(stored, financeRoot)
  if (!found) return new Response(`${label} file not found or its name is ambiguous. The question is still open.`, { status: 404, headers })
  const filePath = realpathSync(found)
  const roots = ['readable_documents/receipts', 'readable_documents/generated_spreadsheets',
    'receipt_parsing_tools/generated_spreadsheets',
    ...(pdf ? ['readable_documents/scanned_statements', 'readable_documents/bank_statements'] : [])]
    .map(path => resolve(financeRoot, path)).filter(existsSync).map(path => realpathSync(path))
  if (!roots.some(root => {
    const path = relative(root, filePath)
    return path !== '' && path !== '..' && !path.startsWith('../') && !isAbsolute(path)
  })) return new Response(`${label} is outside the allowed document folders.`, { status: 403, headers })
  const type = mimeTypes[extname(filePath).toLowerCase()]
  if (pdf && type !== 'application/pdf') return new Response('This linked file is not a PDF.', { status: 415, headers })
  if (!type) return new Response('This receipt format cannot be displayed here. Use Show receipt in the terminal.', { status: 415, headers })
  return new Response(Bun.file(filePath), { headers: { ...headers, 'content-type': type,
    'content-disposition': 'inline' } })
}
