import type { EventRow } from '../../lib/types'
import { fmtClock } from '../../lib/format'
import { parsePayload } from '../../lib/events'
import type { LogEntry } from '../log-object/log-entry'
import { documentUrl, markdownPaths, remainingWorkUrl } from '../../../shared/report-links'

function fieldsText(fields: Record<string, unknown>): string {
  return Object.entries(fields).map(([key, value]) => {
    const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2)
    return text.includes('\n') ? `${key}:\n${text}` : `${key}: ${text}`
  }).join('\n')
}

function payloadText(raw: string | null): string {
  if (!raw) return ''
  try {
    const value: unknown = JSON.parse(raw)
    if (typeof value === 'string') return value
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      const { message, level: _level, ...rest } = value as Record<string, unknown>
      // The level lives in the header. Messages and tool output keep real newlines.
      if (typeof message === 'string') {
        const extra = Object.keys(rest).length ? `\n${fieldsText(rest)}` : ''
        return `${message}${extra}`
      }
      return fieldsText(value as Record<string, unknown>)
    }
    return JSON.stringify(value, null, 2)
  } catch {
    return raw
  }
}

export function eventLogs(events: EventRow[]): LogEntry[] {
  return events.toSorted((a, b) => a.rowid - b.rowid).map((event) => {
    const payload = parsePayload(event.payload_json)
    const level = typeof payload?.level === 'string' ? ` · ${payload.level}` : ''
    const reportUrl = remainingWorkUrl(event.adw_id)
    // Old runs logged only the Markdown path. Their converted HTML uses the same route.
    const hasReport = reportUrl && (payload?.report_url === reportUrl
      || (typeof payload?.report === 'string'
        && (payload.report.endsWith(`/sessions/${event.adw_id}/remaining-work.html`)
          || payload.report.endsWith(`/sessions/${event.adw_id}/remaining-work.md`))))
    let message = payloadText(event.payload_json)
    const links = hasReport ? [{ label: 'Open remaining work report', href: reportUrl }] : []
    for (const path of markdownPaths(message)) {
      const display = path.replace(/\.md$/, '.html')
      message = message.replaceAll(path, display)
      if (hasReport && path.endsWith(`/sessions/${event.adw_id}/remaining-work.md`)) continue
      links.push({ label: `Open ${display.split('/').at(-1)}`, href: documentUrl(path, event.adw_id) })
    }
    return {
      id: event.event_id,
      timestamp: fmtClock(event.started_at),
      method: `[${event.type ?? 'event'}] ${event.name ?? ''}${level}`.trim(),
      message,
      ...(links.length ? { links } : {}),
      status: event.type === 'error' || event.type === 'gate_fail' || payload?.ok === false
        || payload?.status === 'fail' || payload?.level === 'error' ? 'error'
        : event.type === 'gate_pass' || payload?.status === 'success' ? 'success' : 'info',
    }
  })
}
