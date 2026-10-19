import { expect, test } from 'bun:test'
import { mkdtempSync, mkdirSync, writeFileSync, symlinkSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { markdownResponse } from './markdown'
import { remainingWorkResponse } from './index'

test('renders project and session Markdown, including tables and links, without active content', async () => {
  const root = mkdtempSync(join(tmpdir(), 'markdown-report-'))
  try {
    const sessions = join(root, 'adws/adw_data/sessions')
    const session = join(sessions, 'run')
    mkdirSync(session, { recursive: true })
    writeFileSync(join(root, 'report with spaces.md'), `# What remains

**Check** these records.

| Work | Count |
| --- | --- |
| Categories | 638 |

- [x] Mileage
- [ ] Review

[Related](adws/adw_data/sessions/run/notes.md)
[Unsafe](javascript:alert%281%29)
<script>alert(1)</script>
![Tracking](https://example.com/track)

\`\`\`html
<img src=x onerror=alert(1)>
\`\`\`
`)
    writeFileSync(join(session, 'notes.md'), '## Session notes')
    const response = markdownResponse(root, sessions, 'report with spaces.md', 'run')
    expect(response.status).toBe(200)
    const body = await response.text()
    expect(body).toContain('<h1>What remains</h1>')
    expect(body).toContain('<table>')
    expect(body).toContain('<td>638</td>')
    expect(body).toContain('<strong>Check</strong>')
    expect(body).toContain('☑')
    expect(body).toContain('/api/documents/view.html?path=')
    expect(body).not.toContain('href="javascript:')
    expect(body).not.toContain('<script>')
    expect(body).not.toContain('<img')
    expect(body).toContain('&lt;img')
    expect(response.headers.get('content-security-policy')).toContain("default-src 'none'")
    expect((await markdownResponse(root, sessions, 'notes.md', 'run').text())).toContain('<h2>Session notes</h2>')
    expect(markdownResponse(root, sessions, 'missing.md', 'run').status).toBe(404)
    expect(markdownResponse(root, sessions, '.env', 'run').status).toBe(400)
    expect(markdownResponse(root, sessions, 'notes.md', '../run').status).toBe(400)

    const outside = root + '-private.md'
    writeFileSync(outside, 'private')
    try {
      symlinkSync(outside, join(root, 'escape.md'))
      expect(markdownResponse(root, sessions, 'escape.md', 'run').status).toBe(403)
      expect(markdownResponse(root, sessions, '../' + outside.split('/').at(-1), 'run').status).toBe(404)
    } finally { rmSync(outside) }

    writeFileSync(join(session, 'remaining-work.md'), '# Old progress report')
    expect(await remainingWorkResponse(sessions, 'run').text()).toContain('<h1>Old progress report</h1>')
  } finally { rmSync(root, { recursive: true, force: true }) }
})
