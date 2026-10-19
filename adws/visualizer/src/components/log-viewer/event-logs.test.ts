import { describe, expect, test } from 'bun:test'
import type { EventRow } from '../../lib/types'
import { eventLogs } from './event-logs'

function event(overrides: Partial<EventRow> = {}): EventRow {
  return {
    rowid: 1, event_id: 'one', adw_id: 'run', phase_id: null, parent_id: null,
    type: 'log', name: 'console', payload_json: null, tokens: null,
    started_at: '2026-10-16T18:00:00Z', ended_at: null, ...overrides,
  }
}

describe('terminal event adapter', () => {
  test('turns Markdown references in console text and nested payloads into HTML links', () => {
    const [log] = eventLogs([event({ payload_json: JSON.stringify({
      message: 'Read `readable_documents/report with spaces.md` and adws/notes.md.',
      sources: ['/home/adamsl/rol_finances/plan.md', 'adws/notes.md'],
    }) })])
    expect(log!.message).toContain('readable_documents/report with spaces.html')
    expect(log!.message).toContain('adws/notes.html.')
    expect(log!.links?.map(link => link.label)).toEqual(['Open report with spaces.html', 'Open notes.html', 'Open plan.html'])
    expect(log!.links?.[0]?.href).toContain('readable_documents%2Freport%20with%20spaces.md')
    expect(log!.links?.[0]?.href).toContain('session=run')
    for (const message of ['https://example.com/plan.md', 'javascript:bad.md', '//example.com/doc.md']) {
      expect(eventLogs([event({ payload_json: JSON.stringify({ message }) })])[0]!.links).toBeUndefined()
    }
  })

  test('links new HTML reports and converted Markdown reports to this session only', () => {
    const url = '/api/sessions/run/reports/remaining-work.html'
    for (const payload of [
      { report_url: url },
      { report: 'adws/adw_data/sessions/run/remaining-work.html' },
      { report: 'adws/adw_data/sessions/run/remaining-work.md' },
    ]) {
      const [log] = eventLogs([event({ payload_json: JSON.stringify(payload) })])
      expect(log!.links).toEqual([{ label: 'Open remaining work report', href: url }])
    }
    for (const payload of [
      { report_url: 'javascript:alert(1)' },
      { report_url: 'https://other.example/report.html' },
      { report: 'adws/adw_data/sessions/other/remaining-work.html' },
      { message: '<a href="javascript:alert(1)">fake</a>' },
    ]) {
      expect(eventLogs([event({ payload_json: JSON.stringify(payload) })])[0]!.links).toBeUndefined()
    }
  })

  test('keeps console newlines, metadata, and literal HTML without truncating', () => {
    const message = `<img src=x onerror=alert(1)>\n${'Long output '.repeat(1000)}`
    const [log] = eventLogs([event({payload_json: JSON.stringify({message, level:'info', source:'file.xlsx'})})])
    expect(log!.message).toBe(`${message}\nsource: file.xlsx`)
    expect(log!.method).toContain('info')
  })

  test('includes every phase and orders by insertion cursor without mutating input', () => {
    const rows = [event({rowid:3,event_id:'three',phase_id:'other'}), event()]
    expect(eventLogs(rows).map(log => log.id)).toEqual(['one','three'])
    expect(rows[0]!.event_id).toBe('three')
  })

  test('shows tool arguments and real multiline results without clicking', () => {
    const [log] = eventLogs([event({type:'tool_call',payload_json:JSON.stringify({tool:'bash',args:{command:'ls'},result_snippet:'first\nlast',ok:false})})])
    expect(log!.message).toContain('"command": "ls"')
    expect(log!.message).toContain('result_snippet:\nfirst\nlast')
    expect(log!.status).toBe('error')
  })

  test('preserves malformed, legacy, scalar, and empty payloads', () => {
    for (const raw of ['{unfinished', 'ordinary text', null, '"text"', '[1,2]', 'null', 'false']) {
      const [log] = eventLogs([event({payload_json:raw})])
      expect(log!.message).toBe(raw === null ? '' : raw === '"text"' ? 'text' : raw === '[1,2]' ? '[\n  1,\n  2\n]' : raw)
    }
  })

  test('uses recorded outcomes, not words like fail inside a passing command', () => {
    expect(eventLogs([event({type:'phase_end',payload_json:'{"status":"fail"}'})])[0]!.status).toBe('error')
    expect(eventLogs([event({type:'gate_pass',payload_json:'{"command":"test-failure-handling"}'})])[0]!.status).toBe('success')
  })
})
