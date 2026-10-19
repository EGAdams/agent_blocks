/** On-demand presentation of existing console events. No agent, file writes, or saves. */
import type { Event, Session } from '../../shared/types'
import css from './style.css' with { type: 'text' }

export interface QuestionSource { path: string; details: string[] }
export interface PendingQuestion {
  title: string
  question: string
  why: string
  evidence: string[]
  choices: string[]
  reply: string[]
  answer: string
  calculation: string
  nextStep: string
  notes: string[]
  sources: QuestionSource[]
  status: string
}

function isCategoryReview(question: PendingQuestion | null): boolean {
  return Boolean(question && (question.question.startsWith('Which specific category fits payment ')
    || question.sources.some(source => source.path.startsWith('nonprofit_finance.expenses:'))))
}

export function consoleMessages(events: Event[]): string[] {
  return events.toSorted((a, b) => a.rowid - b.rowid).flatMap(event => {
    if (event.type !== 'log' || !event.payload_json) return []
    try {
      const payload: unknown = JSON.parse(event.payload_json)
      // _emit writes a level; internal ph.log notes have no level and were never
      // printed to the user's console. Keep that machinery out of this page.
      if (payload && typeof payload === 'object' && 'level' in payload
          && 'message' in payload && typeof payload.message === 'string') {
        return [payload.message]
      }
    } catch { /* Ignore unrelated or damaged historical event payloads. */ }
    return []
  })
}

export function pendingQuestion(messages: string[], sessionStatus: string | null): PendingQuestion | null {
  let result: PendingQuestion | null = null
  let background = false
  let checking = false
  for (const message of messages) {
    if (message.startsWith('ONE QUESTION: ')) {
      result = { title: message.slice(14), question: '', why: '', evidence: [], choices: [], reply: [],
        answer: '', calculation: '', nextStep: '', notes: [], sources: [], status: 'Open · nothing saved' }
      background = false
      checking = false
      continue
    }
    if (!result) continue
    if (message.startsWith('Why we are asking: ')) {
      result.why = message.slice(19)
      background = true
    }
    else if (message === 'What we found:') background = true
    else if (message.startsWith('Your question: ')) {
      result.question = message.slice(15)
      background = false
    } else if (message.startsWith('The database record changed.')) {
      result.answer = ''
      result.status = 'Record refreshed · nothing saved'
      result.reply.push(message)
      result.evidence = []
      result.sources = result.sources.filter(source =>
        !source.details.some(label => ['Receipt', 'Document', 'Statement', 'Check image PDF'].includes(label)))
    } else if (message.startsWith('Date: ') && isCategoryReview(result)) {
      result.evidence = [message]
    } else if (/^(?:\d+|Q)\. /.test(message)) {
      for (const choice of message.split(/\n|\s{2,}(?=(?:\d+|Q)\. )/)) {
        if (!result.choices.includes(choice)) result.choices.push(choice)
      }
    }
    else if (message.startsWith('Source: ')) {
      const [path = '', ...locations] = message.slice(8).split(' — ')
      let source = result.sources.find(item => item.path === path)
      if (!source) { source = { path, details: [] }; result.sources.push(source) }
      const detail = locations.join(' — ')
      if (detail && !source.details.includes(detail)) source.details.push(detail)
    } else if (/^(Receipt|Document|Statement|Check image PDF): /.test(message)) {
      result.sources.push({ path: message.slice(message.indexOf(': ') + 2), details: [message.split(': ')[0]!] })
    } else if (message.startsWith('Source lookup: ')) result.notes.push(message.slice(15))
    else if (message.startsWith('PDF info: ')) result.reply.push(message.slice(10))
    else if (message.startsWith('Transfer info: ')) result.reply.push(message.slice(15))
    else if (message.startsWith('Your message: ') || message.startsWith('Previous message for this payment: ')) {
      result.reply.push(message)
      result.answer = ''
      result.status = 'Open · nothing saved'
    } else if (message.startsWith('Reply: ')) {
      result.reply.push(message.slice(7))
      if (isCategoryReview(result)) {
        result.answer = ''
        result.status = 'Open · nothing saved'
      }
    }
    else if (background) result.evidence.push(message)
    else if (message.startsWith('Checking your message')) {
      // Old proposals must not look current while a correction is being checked.
      result.reply = []
      result.answer = ''
      result.calculation = ''
      result.nextStep = ''
      result.sources = result.sources.filter(source => !source.details.length)
      result.status = 'Checking · nothing saved'
      checking = true
    } else if (message.startsWith('Possible answer: ') || message.startsWith('I will save: ')) {
      result.answer = message.replace(/^(Possible answer|I will save): /, '')
      result.status = 'Awaiting your approval · nothing saved'
    } else if (message.startsWith('How I got it: ')) result.calculation = message.slice(14)
    else if (message.startsWith('Next step: ')) result.nextStep = message.slice(11)
    else if (message.startsWith('Saved.')) result.status = 'Answer recorded · follow-up work remains'
    else if (message.startsWith('Stopped.') || message.startsWith('Cancelled.')) result.status = 'Stopped · no answer saved'
    else if (message.startsWith('This step stopped:') || message.startsWith('This visit stopped.')) {
      result.status = 'Visit stopped · question still open'
      result.notes.push(message)
    } else if (message.startsWith('Nothing saved.') || message.startsWith('No answer saved.')) {
      result.status = 'Open · nothing saved'
    } else if (message.startsWith('Read: ') || message.includes('formula cells') || message.startsWith('Numeric Excel dates')
        || message.startsWith('Checked ') || message.startsWith('I cannot find') || message.startsWith('A source changed')) {
      if (!result.notes.includes(message)) result.notes.push(message)
      if (message.startsWith('A source changed')) { result.answer = ''; result.status = 'Source changed · check again' }
    } else if (/^(This step can change|This reminder|No vendor rule|Prepared draft:|Still open:)/.test(message)) {
      result.notes.push(message)
    } else if (checking && !/^(Talk normally|Still working|Run just|This visit|Still checking|Still preparing|Message kept|Status:)/.test(message)) {
      result.reply.push(message)
      if (!result.answer) result.status = 'Open · nothing saved'
    }
  }
  if (result && sessionStatus !== 'running' && !result.status.startsWith('Answer recorded')) {
    result.status = 'Visit ended · question still open'
  }
  return result
}

function escape(value: string): string {
  return value.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!)
}

function paragraphs(value: string): string[] {
  return value.split(/(?<=[.!?])\s+(?=[A-Z0-9])/)
}

function details(value: string): string {
  return paragraphs(value).map(part => {
    const pair = part.match(/^(.+?):\s*([\d,]+(?:\.\d+)?)(?: miles)?\.?$/)
    return pair ? `<div class="figure"><span>${escape(pair[1]!)}</span><strong>${escape(pair[2]!)} <small>miles</small></strong></div>`
      : `<p>${escape(part)}</p>`
  }).join('')
}

function section(title: string, content: string, className = ''): string {
  return content ? `<section class="${className}"><h2>${title}</h2>${content}</section>` : ''
}

function paymentDetails(evidence: string[]): string {
  const record = evidence.find(item => item.startsWith('Date: '))
  if (!record) return evidence.map(item => `<p class="record">${escape(item)}</p>`).join('')
  const date = record.match(/^Date: ([^.]+)\. Amount: \$([\d,.]+)\./)
  const fields = record.split('\n').slice(1).map(line => {
    const colon = line.indexOf(': ')
    return [line.slice(0, colon), line.slice(colon + 2)]
  })
  const description = fields.find(([key]) => key === 'Description')?.[1] ?? ''
  return `<table class="payment-table"><thead><tr><th>Description</th><th>Date</th><th>Amount</th></tr></thead>
    <tbody><tr><td class="description">${escape(description)}</td><td>${escape(date?.[1] ?? '')}</td><td>$${escape(date?.[2] ?? '')}</td></tr></tbody></table>
    <dl>${fields.filter(([key]) => key !== 'Description').map(([key, value]) => `<dt>${escape(key!)}</dt><dd>${escape(value!)}</dd>`).join('')}</dl>`
}

export function linkedPdfs(question: PendingQuestion | null): QuestionSource[] {
  return (question?.sources ?? []).filter(source => {
    if (!source.details.some(label => ['Statement', 'Document', 'Receipt'].includes(label))) return false
    try { return /\.pdf$/i.test(decodeURIComponent(new URL(source.path, 'file:///').pathname)) }
    catch { return false }
  }).filter((source, index, sources) => sources.findIndex(item => item.path === source.path) === index)
}

export function renderQuestionPage(question: PendingQuestion | null, session: Session, checkImagePdf = ''): string {
  const refresh = `/api/sessions/${encodeURIComponent(session.adw_id)}/pending-question`
  const receiptUrl = `/api/sessions/${encodeURIComponent(session.adw_id)}/receipt`
  const hasReceipt = question?.sources.some(source => source.details.includes('Receipt'))
  const pdfLinks = linkedPdfs(question).map((source, index) =>
    `<a class="receipt-button" href="/api/sessions/${encodeURIComponent(session.adw_id)}/pdf?index=${index}" target="_blank" rel="noopener noreferrer">View PDF · ${escape(source.details[0] ?? 'Document')}</a>`).join(' ')
  const categoryReview = isCategoryReview(question)
  const hasCheckImage = Boolean(checkImagePdf || question?.sources.some(source => source.details.includes('Check image PDF')))
  const checkLinks = categoryReview ? `${hasCheckImage ? `<a class="receipt-button" href="/api/sessions/${encodeURIComponent(session.adw_id)}/check-image-pdf" target="_blank" rel="noopener noreferrer">Open check image PDF</a>` : ''}
    <a class="receipt-button" href="/api/sessions/${encodeURIComponent(session.adw_id)}/pdf-info" target="_blank" rel="noopener noreferrer">Get Info from PDF</a>` : ''
  const replyHelp = categoryReview
    ? 'Type 8 or M to Type Message, or type a sentence directly in the terminal. The assistant can prepare a category preview directly. Type ? for the menu. Type 4 to show the receipt, 5 to view the PDF, 6 to open check images, 7 to read PDF information, or 10 to check transfer activity and find a check payee. Option 1 keeps a valid category (including Personal), or helps narrow a broad category. Type 2 to choose a category, or 3 to ask Rosemary and move on. Unavailable documents are labeled. Only YES at the preview records your review of this one payment. Q stops without saving.'
    : 'Ask a question or type P to preview the checked answer. Only typing YES at the confirmation prompt saves an answer.'
  const content = question ? `
    <header><p class="eyebrow">${escape(question.title)}</p><h1>${escape(question.question || question.title)}</h1>
      <p class="status">${escape(question.status)}</p></header>
    ${hasReceipt || pdfLinks || checkLinks ? `<p class="document-links">${hasReceipt ? `<a class="receipt-button" href="${receiptUrl}" target="_blank" rel="noopener noreferrer">Show receipt</a>` : ''} ${pdfLinks} ${checkLinks}</p>` : ''}
    ${categoryReview ? section('Payment details', paymentDetails(question.evidence)) : ''}
    ${section(categoryReview ? 'What needs attention' : 'Why we are asking', `<p>${escape(question.why)}</p>`, categoryReview ? 'attention' : '')}
    ${section('Latest reply &amp; checked figures', question.reply.map(details).join(''))}
    ${section('Proposed answer', question.answer ? details(question.answer) : '', 'answer')}
    ${section('How it was calculated', question.calculation ? details(question.calculation) : '')}
    ${section('Later follow-up', question.nextStep ? `<p>${escape(question.nextStep)}</p>` : '')}
    <aside><strong>Reply in your terminal.</strong> ${replyHelp} This page cannot save or change anything.</aside>
    ${section('Other ways to respond', question.choices.length ? `<ul>${question.choices.map(item => `<li>${escape(item)}</li>`).join('')}</ul>` : '')}
    ${categoryReview ? '' : section('Background records', question.evidence.map(item => `<p class="record">${escape(item)}</p>`).join(''))}
    ${section('Sources', question.sources.map(source => `<article><h3>${escape(source.path.split('/').pop() || source.path)}</h3>
      <p class="path">${escape(source.path)}</p>${source.details.length ? `<ul>${source.details.map(detail => `<li>${escape(detail)}</li>`).join('')}</ul>` : ''}</article>`).join(''))}
    ${section('Source notes', question.notes.map(item => `<p>${escape(item)}</p>`).join(''), 'notes')}
    ` : '<header><h1>No question recorded yet</h1><p>Select a tax-question session in the viewer, or refresh after the workflow asks its question.</p></header>'
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Pending Question · ${escape(session.adw_id)}</title><style>${css}</style></head><body>
    <nav><strong>Pending Question</strong><a href="${refresh}">Refresh question</a></nav>
    <main>${content}<footer>Session ${escape(session.adw_id)} · Snapshot ${escape(new Date().toLocaleString('en-US'))}.
      Click Refresh question to see later messages.</footer></main></body></html>`
}
