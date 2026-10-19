import { expect, test } from 'bun:test'
import { pendingQuestion, renderQuestionPage } from '../pending-question'
import { pdfRequest, renderPdfInfo } from './index'
import type { Session } from '../../shared/types'

const messages = ['ONE QUESTION: Utilities · payment 2945', 'Why we are asking: Check the bill.',
  'Date: 2025-08-14. Amount: $122.19.\nDescription: Consumers - Check 3923\nCurrent category: Personal\nRecord: 3923_08_14_25_122_19',
  'Your question: Which specific category fits payment 2945?', 'Statement: /statements/5938.pdf',
  'Check image PDF: /checks/august_images.pdf', '6. Open check image PDF.', '7. Get Info from PDF.']
const session = { adw_id: 'test-run', status: 'running' } as Session

test('derives typed PDF request from selected payment, not user-supplied URL paths', () => {
  const question = pendingQuestion(messages, 'running')
  expect(pdfRequest(question)).toEqual({ statement: '/statements/5938.pdf', document: '', receipt: '',
    expense_date: '2025-08-14', amount: '122.19', description: 'Consumers - Check 3923', record: '3923_08_14_25_122_19' })
  expect(pdfRequest(pendingQuestion(['ONE QUESTION: Income', 'Your question: What was income?'], 'running'))).toBeNull()
  expect(pdfRequest(null)).toBeNull()
})

test('Pending Question links to check images and read-only on-demand PDF information', () => {
  const question = pendingQuestion(messages, 'running')!
  expect(question.sources.some(source => source.details.includes('Check image PDF'))).toBe(true)
  const html = renderQuestionPage(question, session)
  expect(html).toContain('href="/api/sessions/test-run/check-image-pdf"')
  expect(html).toContain('href="/api/sessions/test-run/pdf-info"')
  expect(html).toContain('6 to open check images')
  expect(html).not.toContain('src="/api/sessions/test-run/pdf-info')
  // Historical sessions can obtain the link from lookup, without changing logs.
  const older = pendingQuestion(messages.filter(message => !message.startsWith('Check image PDF:')), 'running')!
  expect(renderQuestionPage(older, session, '/checks/august_images.pdf')).toContain('>Open check image PDF</a>')
  expect(renderQuestionPage(older, session)).not.toContain('>Open check image PDF</a>')
})

test('natural-language category questions retain PDF tools and recovered statement links', () => {
  const question = pendingQuestion([
    'ONE QUESTION: Check the payment category · payment 1179',
    'Why we are asking: Gifts & Love Offerings is a broad group. Choose a recipient category.',
    'Date: 2025-01-02. Amount: $156.00.\nDescription: Check 7036\nCurrent category: Church / Gifts & Love Offerings\nRecord: check_7036',
    'Source: nonprofit_finance.expenses:1179',
    'Your question: What was this purchase for?',
    'Source lookup: Found a matching PDF; the database is unchanged.',
    'Statement: /archive/3119.pdf',
    '1. Keep this category.\n5. View PDF.\n8. Type Message.',
  ], 'running')!
  expect(pdfRequest(question)?.statement).toBe('/archive/3119.pdf')
  expect(question.choices).toEqual(['1. Keep this category.', '5. View PDF.', '8. Type Message.'])
  const html = renderQuestionPage(question, session)
  expect(html).toContain('<th>Description</th>')
  expect(html).toContain('<td class="description">Check 7036</td>')
  expect(html).toContain('What needs attention')
  expect(html).toContain('Found a matching PDF; the database is unchanged.')
  expect(html).toContain('/api/sessions/test-run/pdf?index=0')
})

test('PDF information preserves source wording, escapes OCR, and links the matched page', () => {
  const html = renderPdfInfo({ check_image_pdf: '/checks/august_images.pdf', pages: [1],
    lines: ['Page 1: check 3923, $122.19.', '<script>alert(1)</script>'] }, 'test-run', 'Which category?')
  expect(html).toContain('/check-image-pdf#page=1')
  expect(html).toContain('Page 1: check 3923, $122.19.')
  expect(html).toContain('&lt;script&gt;')
  expect(html).not.toContain('<script>')
  expect(html).toContain('Nothing saved.')
})
