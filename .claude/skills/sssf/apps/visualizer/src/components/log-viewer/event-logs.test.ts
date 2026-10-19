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
