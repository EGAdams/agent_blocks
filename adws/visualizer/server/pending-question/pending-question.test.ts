import { expect, test } from 'bun:test'
import type { Event, Session } from '../../shared/types'
import { consoleMessages, linkedPdfs, pendingQuestion, renderQuestionPage } from './index'

const session: Session = { adw_id: 'test-run', adw_name: 'tax', request: '', status: 'running',
  engineer: 'EG', started_at: null, ended_at: null, total_tokens: 0, total_cost: 0, archived: 0 }
const intro = ['ONE QUESTION: Church trips in the 2016 van', 'Why we are asking: Separate the trips.',
  'What we found:', 'The odometer changed by 12,850 miles.', 'Source: /notes.txt',
  'Your question: How many church miles did the 2016 van travel?', '1. I have a trip record.',
  'Checking your message and remembered files for this question...']
const summary = "2011 van (RJ): 1,187.6 miles. 2016 van (RM or no tag): 2,276.2 miles. Both vans' church trips: 3,463.8 miles. This matches the spreadsheet total."
const proposal = ['Possible answer: Church miles in 2025 for this van: 2,276.2. Odometer change: 12,850 miles. That remainder is not a confirmed personal-mileage figure.',
  'How I got it: Added each round-trip cell once.', 'Source: /mileage.xlsx — Mileage 2025!D2: 4.8',
  'Source: /mileage.xlsx — Mileage 2025!D5: 4.8', 'Next step: Review remaining mileage.']

test('separates the question, figures, source locations, and unsaved proposal', () => {
  const question = pendingQuestion([...intro, summary, ...proposal], 'running')!
  expect(question.question).toBe('How many church miles did the 2016 van travel?')
  expect(question.why).toBe('Separate the trips.')
  expect(question.calculation).toBe('Added each round-trip cell once.')
  expect(question.evidence).toEqual(['The odometer changed by 12,850 miles.'])
  expect(question.sources.find(source => source.path === '/mileage.xlsx')!.details.length).toBe(2)
  const html = renderQuestionPage(question, session)
  expect(html.match(/class="figure"/g)?.length).toBe(5)
  expect(html).toContain('1,187.6')
  expect(html).toContain('2,276.2')
  expect(html).toContain('3,463.8')
  expect(html).toContain('not a confirmed personal-mileage figure')
  expect(html).toContain('Awaiting your approval')
  expect(html).not.toContain('<script')
})

test('a new check clears the earlier proposal before any new results arrive', () => {
  const question = pendingQuestion([...intro, summary, ...proposal,
    'Message kept for later questions. No answer saved.',
    'Checking your message and remembered files for this question...'], 'running')!
  expect(question.answer).toBe('')
  expect(question.reply).toEqual([])
  expect(question.sources.some(source => source.path === '/mileage.xlsx')).toBe(false)
  expect(question.status).toStartWith('Checking')
})

test('saving and stopping are distinguished from an open question', () => {
  expect(pendingQuestion([...intro, ...proposal, 'Saved. Your answer is recorded; the follow-up work is still open.'], 'success')!.status).toStartWith('Answer recorded')
  expect(pendingQuestion([...intro, ...proposal, 'Stopped. No answer saved.'], 'success')!.status).toBe('Visit ended · question still open')
  expect(pendingQuestion([...intro, ...proposal, 'No answer saved. Keep talking.'], 'running')!.status).toStartWith('Open')
})

test('a changed source removes the preview; a different question cannot inherit it', () => {
  expect(pendingQuestion([...intro, ...proposal, 'A source changed while we were reviewing it. Nothing saved; check it again with Type Message.'], 'running')!.answer).toBe('')
  const different = pendingQuestion([...intro, ...proposal, 'ONE QUESTION: Honoraria', 'Your question: Who received it?'], 'running')!
  expect(different.title).toBe('Honoraria')
  expect(different.answer).toBe('')
  expect(different.sources).toEqual([])
})

test('source contents and user text are escaped, never interpreted as HTML', () => {
  const question = pendingQuestion([...intro, '<img src=x onerror=alert(1)>',
    'Source: /<script>.xlsx — A2: <iframe src="bad">', 'Possible answer: <b>literal</b>'], 'running')!
  const html = renderQuestionPage(question, session)
  expect(html).toContain('&lt;img')
  expect(html).toContain('&lt;script&gt;.xlsx')
  expect(html).toContain('&lt;b&gt;literal&lt;/b&gt;')
  expect(html).not.toContain('<iframe')
  expect(html).not.toContain('<img')
})

test('unrelated sessions have a clear empty state', () => {
  expect(pendingQuestion(['Other workflow output'], 'success')).toBeNull()
  expect(renderQuestionPage(null, session)).toContain('No question recorded yet')
  expect(consoleMessages([])).toEqual([])
})

test('plain purchase wording retains category actions and refreshed payment details', () => {
  const question = pendingQuestion([
    'ONE QUESTION: Check the payment category · payment 2123',
    'Why we are asking: Put the purchase in the right group.',
    'Date: 2025-01-01. Amount: $81.31.\nDescription: Amazon\nCurrent category: Uncategorized',
    'Source: nonprofit_finance.expenses:2123',
    'Your question: What was this purchase for — church work, Robert and Rosemary\'s home, or personal items?',
    'I will save: Change to Housing',
    'The database record changed. I refreshed payment 2123. Current category: Personal.',
    'Date: 2025-01-01. Amount: $81.31.\nDescription: Amazon\nCurrent category: Personal',
    'Reply: Your explanation is remembered.',
  ], 'running')!
  expect(question.answer).toBe('')
  expect(question.evidence).toHaveLength(1)
  expect(question.evidence[0]).toContain('Current category: Personal')
  const html = renderQuestionPage(question, session)
  expect(html).toContain('Payment details')
  expect(html).toContain('8 or M to Type Message')
  expect(html).toContain('Get Info from PDF')
  expect(html).not.toContain('type P to preview')
  expect(html).not.toContain('Current category: Uncategorized')
})

test('internal phase notes and structured debug payloads never become user-facing text', () => {
  const event: Event = { rowid: 1, event_id: 'event', adw_id: 'test-run', phase_id: null,
    parent_id: null, type: 'log', name: 'console', payload_json: null,
    tokens: null, started_at: null, ended_at: null }
  const messages = consoleMessages([
    { ...event, payload_json: JSON.stringify({message:'Visible question',level:'info'}) },
    { ...event, payload_json: JSON.stringify({message:'issue: van, saved: False, source_hashes: {...}'}) },
    { ...event, payload_json: '{broken' },
  ])
  expect(messages).toEqual(['Visible question'])
})

test('category review includes payment details, receipt, choices, and category confirmation help', () => {
  const question = pendingQuestion([
    'ONE QUESTION: Separate church and house utility bills · payment 2159',
    'Why we are asking: Identify the building from its bill.',
    'Date: 2025-04-23. Amount: $13.71.\nDescription: at_t\nCurrent category: Church / Office\nRecord: at_t_04_23_25_13_71',
    'Source: nonprofit_finance.expenses:2159',
    'Your question: Which specific category fits payment 2159?',
    'Receipt: at_t_04_23_25_13_71.jpg',
    "This step can change this ONE payment's database category after you type YES.",
    '1. Keep this category.  2. Choose a category.  3. Ask Rosemary and move on.  Q. Stop.',
  ], 'running')!
  expect(question.choices.length).toBe(4)
  const html = renderQuestionPage(question, session)
  for (const value of ['Payment details', '$13.71', 'Church / Office', 'at_t_04_23_25_13_71.jpg', 'this one payment']) {
    expect(html).toContain(value)
  }
  expect(html).not.toContain('type P')
  expect(html).toContain('href="/api/sessions/test-run/receipt"')
  expect(html).toContain('>Show receipt</a>')
  expect(html).not.toContain('13.71 <small>miles')
  const changed = pendingQuestion([
    'ONE QUESTION: Utility bill', 'Your question: Which specific category fits payment 2159?',
    'I will save: Payment 2159, $13.71: Church / Office → Housing / Housing Utilities / Housing Electric Bill',
    'Source: Service address confirmed by EG',
  ], 'running')!
  expect(changed.status).toBe('Awaiting your approval · nothing saved')
  expect(renderQuestionPage(changed, session)).toContain('Housing Electric Bill')
  expect(renderQuestionPage(changed, session)).not.toContain('>Show receipt</a>')
})

test('statement-only payment gets a PDF link and repeated smart menus stay readable', () => {
  const menu = ['1. Keep this category.', '2. Choose a category.', '3. Ask Rosemary and move on.',
    '4. Show receipt.', '5. View PDF (statement or bill).', 'Q. Stop without changing this payment.']
  const question = pendingQuestion([
    'ONE QUESTION: Utility payment 2945',
    'Your question: Which specific category fits payment 2945?',
    'Statement: /home/adamsl/rol_finances/readable_documents/scanned_statements/2025/august.pdf',
    ...menu, 'Still working on: Which specific category fits payment 2945?', ...menu,
  ], 'running')!
  const html = renderQuestionPage(question, session)
  expect(question.choices).toEqual(menu)
  expect(linkedPdfs(question)).toHaveLength(1)
  expect(html).toContain('href="/api/sessions/test-run/pdf?index=0"')
  expect(html).toContain('>View PDF · Statement</a>')
  expect(html).toContain('5 to view the PDF')
  expect(html).not.toContain('>Show receipt</a>')
  expect(linkedPdfs(pendingQuestion(['ONE QUESTION: Other payment', 'Your question: Which category?'], 'running'))).toEqual([])
})

test('only recorded document links become PDF links; duplicates removed and multiple files indexed', () => {
  const question = pendingQuestion(['ONE QUESTION: Bill', 'Your question: Which category?',
    'Source: /unrelated.pdf', 'Statement: /statement.pdf', 'Receipt: /statement.pdf',
    'Document: https://example.com/bill.PDF?download=1', 'Receipt: /image.jpg'], 'running')!
  expect(linkedPdfs(question).map(source => source.path)).toEqual(['/statement.pdf', 'https://example.com/bill.PDF?download=1'])
  const html = renderQuestionPage(question, session)
  expect(html).toContain('/pdf?index=0')
  expect(html).toContain('/pdf?index=1')
  expect(html).not.toContain('/pdf?index=2')
})

test('payment conversation appears with Type Message help and preserves document links', () => {
  const question = pendingQuestion(['ONE QUESTION: Utility payment 2945',
    'Your question: Which specific category fits payment 2945?',
    'Statement: /august.pdf', 'Check image PDF: /august_images.pdf',
    '8. Type Message (ask a question or tell me something).',
    'Your message: This bill serves <the house>.',
    'Message remembered for this payment. No category changed.',
    'Preparing a reply about this payment…',
    'Reply: Choose 2, then category 314 for house electricity.',
  ], 'running')!
  const html = renderQuestionPage(question, session)
  expect(question.reply).toEqual(['Your message: This bill serves <the house>.',
    'Choose 2, then category 314 for house electricity.'])
  expect(question.status).toBe('Open · nothing saved')
  expect(html).toContain('8 or M to Type Message')
  expect(html).toContain('This bill serves &lt;the house&gt;.')
  expect(html).toContain('Choose 2, then category 314')
  expect(html).toContain('/pdf?index=0')
  expect(html).toContain('/check-image-pdf')
})

test('a question about a category preview clears stale approval while the assistant replies', () => {
  const question = pendingQuestion([
    'ONE QUESTION: Utility payment 2679',
    'Your question: Which specific category fits payment 2679?',
    'I will save: Payment 2679: Housing Utilities → Housing Electric Bill',
    'Your message: Why do you need to change it?',
    'Reply: We need to separate home electricity from church electricity. Nothing saved.',
  ], 'running')!
  expect(question.answer).toBe('')
  expect(question.status).toBe('Open · nothing saved')
  const html = renderQuestionPage(question, session)
  expect(html).toContain('prepare a category preview directly')
  expect(html).not.toContain('<h2>Proposed answer</h2>')
})

test('transfer evidence and proposed category are visible in the pending question', () => {
  const question = pendingQuestion([
    'ONE QUESTION: Check the payment category · payment 642',
    'Source: nonprofit_finance.expenses:642',
    'Your question: What was this purchase for?',
    '10. Check transfer activity (find who a check was paid to).',
    'Transfer info: Transfer 494: karen cook · $100.00 · check 11013',
    'Source: /transfer_pay_activity.txt — lines 5424–5433\nTransfer: 494\nTo: karen cook',
    'I will save: Payment 642: Gifts → K Cook [195]',
  ], 'running')!
  const html = renderQuestionPage(question, session)
  expect(question.reply).toContain('Transfer 494: karen cook · $100.00 · check 11013')
  expect(html).toContain('karen cook')
  expect(html).toContain('K Cook [195]')
  expect(html).toContain('transfer_pay_activity.txt')
  expect(html).toContain('10 to check transfer activity')
  expect(html).toContain('Awaiting your approval')
})
