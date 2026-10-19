/** On-demand HTML for local Markdown referenced by monitor logs. No file writes. */
import { existsSync, realpathSync, statSync, readFileSync } from 'node:fs'
import { basename, dirname, isAbsolute, resolve, sep } from 'node:path'
import { documentUrl, remainingWorkUrl } from '../../shared/report-links'

const headers = {
  'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store',
  'content-disposition': 'inline', 'x-content-type-options': 'nosniff',
  'content-security-policy': "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
}
const escape = (value: string) => value.replace(/[&<>"']/g, char => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[char]!))
const within = (file: string, root: string) => file.startsWith(root + sep)

function markdownHtml(text: string, file: string, adwId: string): string {
  return Bun.markdown.render(text, {
    // Bun feeds rendered text into parent callbacks, including code blocks.
    text: escape,
    heading: (children, { level }) => `<h${level}>${children}</h${level}>`,
    paragraph: children => `<p>${children}</p>`,
    strong: children => `<strong>${children}</strong>`,
    emphasis: children => `<em>${children}</em>`,
    strikethrough: children => `<del>${children}</del>`,
    blockquote: children => `<blockquote>${children}</blockquote>`,
    code: children => `<pre><code>${children}</code></pre>`,
    codespan: children => `<code>${children}</code>`,
    list: (children, meta) => meta.ordered
      ? `<ol start="${meta.start ?? 1}">${children}</ol>` : `<ul>${children}</ul>`,
    listItem: (children, meta) => `<li>${meta.checked === undefined ? '' : meta.checked ? '☑ ' : '☐ '}${children}</li>`,
    hr: () => '<hr>',
    table: children => `<div class="table"><table>${children}</table></div>`,
    thead: children => `<thead>${children}</thead>`, tbody: children => `<tbody>${children}</tbody>`,
    tr: children => `<tr>${children}</tr>`, th: children => `<th>${children}</th>`, td: children => `<td>${children}</td>`,
    image: children => `<span>${children}</span>`,
    link: (children, { href }) => {
      let target = href
      if (/\.md$/i.test(href) && !/^[a-z][a-z0-9+.-]*:/i.test(href) && !href.startsWith('//')) {
        try { target = documentUrl(resolve(dirname(file), decodeURIComponent(href)), adwId) }
        catch { return children }
      } else if (!/^(https?:\/\/|#)/i.test(href)) return children
      return `<a href="${escape(target)}" rel="noopener noreferrer">${children}</a>`
    },
  }, { noHtmlBlocks: true, noHtmlSpans: true })
}

export function markdownResponse(repoDir: string, sessionsDir: string, path: string, adwId: string): Response {
  const error = (message: string, status: number) => new Response(escape(message), { status, headers })
  if (!remainingWorkUrl(adwId) || !path.endsWith('.md') || /[\0\r\n]/.test(path)) {
    return error('Invalid Markdown document path or session.', 400)
  }
  const root = realpathSync(repoDir)
  const sessions = realpathSync(sessionsDir)
  const candidates = isAbsolute(path) ? [resolve(path)]
    : [resolve(root, path), resolve(sessions, adwId, path)]
  for (const candidate of candidates) {
    // Both lexical and real-path checks prevent traversal and symlink escapes.
    if (!within(candidate, root) && !within(candidate, sessions)) continue
    if (!existsSync(candidate)) continue
    const file = realpathSync(candidate)
    if (!within(file, root) && !within(file, sessions)) return error('Document is outside the project.', 403)
    const info = statSync(file)
    if (!info.isFile()) continue
    if (info.size > 8 * 1024 * 1024) return error('This document is too large to display (limit 8 MB).', 413)
    const title = basename(file, '.md')
    const body = markdownHtml(readFileSync(file, 'utf8'), file, adwId)
    return new Response(`<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>${escape(title)}</title>
<style>
body { margin:0; background:#eef2f6; color:#182636; font:17px/1.65 system-ui,sans-serif; }
main { max-width:1020px; margin:32px auto; padding:32px; background:white; border-radius:16px; }
h1,h2,h3 { line-height:1.25; margin-top:1.5em; } a { color:#1256a0; }
p,li,.source { overflow-wrap:anywhere; } .source { color:#536376; font-size:.85rem; }
pre,.table { overflow-x:auto; } pre,blockquote { padding:16px; background:#f2f5f9; }
code { font-size:.9em; } table { border-collapse:collapse; width:100%; }
th,td { border:1px solid #ccd6e0; padding:10px; text-align:left; } th { background:#eef4fb; }
@media(max-width:640px) { main { margin:0; padding:20px; border-radius:0; } }
</style></head><body><main><p class="source">HTML view of ${escape(file)} · Read only</p>
${body}</main></body></html>`, { headers })
  }
  return error('Document not found in this project. The log may refer to a file that was moved or removed.', 404)
}
