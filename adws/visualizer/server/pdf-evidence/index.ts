/** Invoke the same local, read-only PDF reader as the terminal, only on demand. */
import { existsSync } from 'node:fs'
import { join } from 'node:path'
import type { PendingQuestion } from '../pending-question'

export interface PdfRequest {
  statement: string; document: string; receipt: string; expense_date: string
  amount: string; description: string; record: string
}
export interface PdfEvidence { check_image_pdf: string; pages: number[]; lines: string[] }
export type PdfReader = (request: PdfRequest, root: string, inspect: boolean) => Promise<PdfEvidence>

export function pdfRequest(question: PendingQuestion | null): PdfRequest | null {
  if (!question || !(question.question.startsWith('Which specific category fits payment ')
    || question.sources.some(source => source.path.startsWith('nonprofit_finance.expenses:')))) return null
  const evidence = [question.why, ...question.evidence].join('\n')
  const date = evidence.match(/(?:^|\n)Date: (\d{4}-\d{2}-\d{2})\. Amount: \$([\d,.]+)\./)
  if (!date) return null
  const source = (label: string) => question.sources.find(item => item.details.includes(label))?.path ?? ''
  return { statement: source('Statement'), document: source('Document'), receipt: source('Receipt'),
    expense_date: date[1]!, amount: date[2]!.replaceAll(',', ''),
    description: evidence.match(/(?:^|\n)Description: ([^\n]*)/)?.[1] ?? '',
    record: evidence.match(/(?:^|\n)Record: ([^\n]*)/)?.[1] ?? '' }
}

export const readPdf: PdfReader = async (request, root, inspect) => {
  const python = join(root, '.venv/bin/python')
  const command = existsSync(python) ? [python] : ['uv', 'run', '--no-project', '--with', 'pydantic', 'python']
  const child = Bun.spawn([...command, '-m', 'adws.adw_modules.tax_work.pdf_evidence',
    inspect ? 'inspect' : 'discover', root], { cwd: root, stdin: 'pipe', stdout: 'pipe', stderr: 'pipe' })
  const timer = setTimeout(() => child.kill(), 60000)
  try {
    child.stdin.write(JSON.stringify(request)); child.stdin.end()
    const [output, , code] = await Promise.all([
      new Response(child.stdout).text(), new Response(child.stderr).text(), child.exited,
    ])
    if (code !== 0) throw new Error('The PDF reader stopped or timed out. The payment is still open.')
    const result: unknown = JSON.parse(output)
    if (!result || typeof result !== 'object' || !('check_image_pdf' in result)
        || typeof result.check_image_pdf !== 'string' || !('pages' in result)
        || !Array.isArray(result.pages) || !result.pages.every(page => Number.isInteger(page) && page > 0)
        || !('lines' in result) || !Array.isArray(result.lines) || !result.lines.every(line => typeof line === 'string')) {
      throw new Error('Invalid PDF reader response')
    }
    return result as PdfEvidence
  } finally { clearTimeout(timer) }
}

const escape = (value: string) => value.replace(/[&<>"']/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!)

export function renderPdfInfo(result: PdfEvidence, sessionId: string, question: string): string {
  const base = `/api/sessions/${encodeURIComponent(sessionId)}`
  const page = result.pages[0] ? `#page=${result.pages[0]}` : ''
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <title>PDF information</title><style>body{font:18px/1.6 system-ui;max-width:1000px;margin:40px auto;padding:0 24px;background:#f4f6fa;color:#17243a}section{background:white;padding:20px;margin:20px 0;border-radius:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:16px/1.5 system-ui}a{margin-right:24px}</style></head><body>
    <a href="${base}/pending-question">Back to Pending Question</a>
    ${result.check_image_pdf ? `<a href="${base}/check-image-pdf${page}" target="_blank" rel="noopener noreferrer">Open check image PDF${page ? ' · matched page' : ''}</a>` : ''}
    <h1>Get Info from PDF</h1><h2>${escape(question)}</h2><p>Read-only evidence. Nothing saved. Review the check image before choosing a category in your terminal.</p>
    ${result.lines.map(line => `<section><pre>${escape(line)}</pre></section>`).join('')}</body></html>`
}
