/** Serve only the generated progress report from the selected session. */
import { existsSync, readFileSync, realpathSync, statSync } from 'node:fs'
import { resolve, sep } from 'node:path'
import { remainingWorkUrl } from '../../shared/report-links'
import { markdownResponse } from './markdown'

/** Upgrade old saved snapshots too, without changing their recorded content. */
export function interactiveReport(html: string): string {
  if (!html.includes('id="expense-summary"')) return html
  if (!html.includes('id="refresh-totals"')) {
    html = html.replace('<h2>Expense summary</h2>', '<h2>Expense summary <button type="button" id="refresh-totals">Refresh Totals</button></h2><p id="refresh-totals-status" role="status" aria-live="polite"></p>')
  }
  if (html.includes('src="/api/reports/refresh.js"')) return html
  return html.replace('</head>', `<style>
#refresh-totals { margin-left:12px; padding:7px 12px; border:1px solid #1457a0; border-radius:6px;
  background:#1457a0; color:white; font:600 .9rem system-ui,sans-serif; cursor:pointer; vertical-align:middle; }
#refresh-totals:disabled { opacity:.65; cursor:wait; }
#refresh-totals-status { margin:0; color:#536376; font-size:.9rem; }
@media print { #refresh-totals,#refresh-totals-status { display:none; } }
</style><script src="/api/reports/refresh.js" defer></script></head>`)
}

export function remainingWorkResponse(sessionsDir: string, adwId: string): Response {
  const headers = {
    'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store',
    'content-disposition': 'inline', 'x-content-type-options': 'nosniff',
    'content-security-policy': "default-src 'none'; script-src 'self'; connect-src 'self'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
  }
  if (!remainingWorkUrl(adwId)) return new Response('Invalid session.', { status: 400, headers })
  const path = resolve(sessionsDir, adwId, 'remaining-work.html')
  if (!existsSync(path)) return markdownResponse(resolve(sessionsDir, '../../..'), sessionsDir,
    resolve(sessionsDir, adwId, 'remaining-work.md'), adwId)
  const root = realpathSync(sessionsDir)
  const sessionRoot = resolve(root, adwId)
  const file = realpathSync(path)
  if (realpathSync(resolve(sessionsDir, adwId)) !== sessionRoot || !file.startsWith(sessionRoot + sep)) {
    return new Response('Report is outside the selected session.', { status: 403, headers })
  }
  if (!statSync(file).isFile()) return new Response('Report not found.', { status: 404, headers })
  return new Response(interactiveReport(readFileSync(file, 'utf8')), { headers })
}
