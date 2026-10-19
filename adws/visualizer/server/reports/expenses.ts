/** Only documents recorded in the selected report can be requested; no path input. */
import { existsSync, readFileSync, realpathSync, statSync } from 'node:fs'
import { basename, extname, resolve, sep } from 'node:path'
import { remainingWorkUrl } from '../../shared/report-links'

const headers = { 'cache-control': 'no-store', 'x-content-type-options': 'nosniff',
  'content-security-policy': "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; sandbox" }

/** Chrome renders a PDF by navigating it into a child frame of its own viewer, so
 *  both `sandbox` (opaque origin, scripting off) and `frame-ancestors 'none'` make the
 *  tab block instead of render. Known inline types get exactly enough to show
 *  themselves — still no network, and only same-origin framing — while anything we do
 *  not recognise keeps the stricter `headers` above and downloads. */
const inlineHeaders = { ...headers,
  'content-security-policy': "default-src 'none'; frame-src 'self'; frame-ancestors 'self'; "
    + "base-uri 'none'; sandbox allow-scripts allow-same-origin" }

export function expenseDocumentResponse(sessionsDir: string, financeRoot: string,
    adwId: string, expenseId: string, index: string): Response {
  const error = (message: string, status: number) => new Response(message, { status,
    headers: { ...headers, 'content-type': 'text/plain; charset=utf-8' } })
  if (!remainingWorkUrl(adwId) || !/^[1-9]\d*$/.test(expenseId) || !/^\d+$/.test(index)) {
    return error('Invalid report document.', 400)
  }
  const session = resolve(realpathSync(sessionsDir), adwId)
  const manifest = resolve(session, 'remaining-expenses.json')
  if (!existsSync(manifest)) return error('No expense documents were recorded for this report. Run just tax-status for a fresh report.', 404)
  if (realpathSync(session) !== session || !realpathSync(manifest).startsWith(session + sep)) {
    return error('Document list is outside the selected session.', 403)
  }
  const records: unknown = JSON.parse(readFileSync(manifest, 'utf8'))
  if (!Array.isArray(records)) return error('Invalid expense document list.', 500)
  const path: unknown = records.find(item => item?.expense?.id === Number(expenseId))?.documents?.[Number(index)]?.path
  if (typeof path !== 'string' || !path) return error('No document is linked at this position in the report.', 404)
  if (/^https?:\/\//i.test(path)) {
    const url = new URL(path)
    if (url.username || url.password) return error('Invalid document link.', 400)
    return new Response(null, { status: 302, headers: { ...headers, location: url.href } })
  }
  if (/^[a-z][a-z\d+.-]*:/i.test(path) || path.startsWith('//')) return error('Unsupported document link.', 400)
  if (!existsSync(path)) return error('This document has moved or was removed. Fix its link in Edit Expense and run just tax-status again.', 404)
  const file = realpathSync(path)
  const roots = ['readable_documents', 'receipt_parsing_tools/generated_spreadsheets']
    .map(folder => resolve(financeRoot, folder)).filter(existsSync).map(folder => realpathSync(folder))
  if (!roots.some(root => file.startsWith(root + sep))) return error('File is outside the document folders.', 403)
  if (!statSync(file).isFile()) return error('Document not found.', 404)
  const types: Record<string, string> = { '.pdf': 'application/pdf', '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp', '.gif': 'image/gif',
    '.txt': 'text/plain; charset=utf-8', '.csv': 'text/plain; charset=utf-8',
    '.md': 'text/plain; charset=utf-8' }
  const type = types[extname(file).toLowerCase()]
  return new Response(Bun.file(file), { headers: { ...(type ? inlineHeaders : headers),
    'content-type': type ?? 'application/octet-stream',
    'content-disposition': `${type ? 'inline' : 'attachment'}; filename*=UTF-8''${encodeURIComponent(basename(file))}` } })
}
