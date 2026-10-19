// Copied from the-factory, then adapted to receive ADW events instead of polling PHP.
import html from './log-viewer.html?raw'
import style from './styles/local_styles.css?raw'
import { LogObject } from '../log-object/log-object.component'
import type { LogEntry } from '../log-object/log-entry'

export class LogViewer extends HTMLElement {
  private screen: HTMLElement
  private list: HTMLOListElement
  private followButton: HTMLButtonElement
  private following = true
  private renderedLogElements = new Map<string, { item: HTMLLIElement; log: LogObject; signature: string }>()

  constructor() {
    super()
    const root = this.attachShadow({ mode: 'open' })
    root.innerHTML = `<style>${style}</style>${html}`
    this.screen = root.querySelector('.screen-area')!
    this.list = root.querySelector('.list-of-log-objects')!
    this.followButton = root.querySelector('.follow')!
    this.screen.addEventListener('scroll', () => {
      this.following = this.screen.scrollHeight - this.screen.clientHeight - this.screen.scrollTop <= 16
      this.updateFollowButton()
    })
    this.followButton.addEventListener('click', () => {
      this.following = !this.following
      if (this.following) this.scrollToLatest()
      this.updateFollowButton()
    })
  }

  set logs(logObjects: LogEntry[]) {
    const ids = new Set(logObjects.map((entry) => entry.id))
    for (const [id, row] of this.renderedLogElements) {
      if (!ids.has(id)) {
        row.item.remove()
        this.renderedLogElements.delete(id)
      }
    }
    // Preserve existing nodes so text selection and scrollback survive polls.
    let previous: HTMLLIElement | null = null
    for (const entry of logObjects) {
      let row = this.renderedLogElements.get(entry.id)
      if (!row) {
        const item = document.createElement('li')
        const log = document.createElement('adw-log-object') as LogObject
        item.append(log)
        row = { item, log, signature: '' }
        this.renderedLogElements.set(entry.id, row)
      }
      const signature = JSON.stringify(entry)
      if (row.signature !== signature) {
        row.log.log = entry
        row.signature = signature
      }
      const next: ChildNode | null = previous ? previous.nextSibling : this.list.firstChild
      if (next !== row.item) this.list.insertBefore(row.item, next)
      previous = row.item
    }
    this.shadowRoot!.querySelector('.log-count')!.textContent = `(${logObjects.length})`
    const empty = this.shadowRoot!.querySelector('.empty') as HTMLElement
    empty.hidden = logObjects.length > 0
    if (this.following) this.scrollToLatest()
  }

  private scrollToLatest() {
    // Move only the terminal, never the surrounding page.
    this.screen.scrollTop = this.screen.scrollHeight
  }

  private updateFollowButton() {
    this.followButton.setAttribute('aria-pressed', String(this.following))
    this.followButton.textContent = this.following ? 'Following latest' : 'Resume live scroll'
  }
}

if (!customElements.get('adw-log-viewer')) customElements.define('adw-log-viewer', LogViewer)
