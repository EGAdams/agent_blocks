import { expect, test } from 'bun:test'
import { financeRoot } from './paths'

test('training viewer keeps finance evidence separate from its playground traces', () => {
  expect(financeRoot('/workspace/agent_blocks/adws/visualizer')).toBe('/workspace/rol_finances')
  expect(financeRoot('/workspace/agent_blocks/adws/visualizer', '/other/finance')).toBe('/other/finance')
})
