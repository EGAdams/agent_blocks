// Adapted from the-factory/src/components/log-object (see ../FACTORY_COMPONENTS.md).
import html from './log-object.html?raw'
import style from './styles/styles.css?raw'
import type { LogEntry } from './log-entry'

export class LogObject extends HTMLElement {
  constructor() {
    super()
    this.attachShadow({ mode: 'open' }).innerHTML = `<style>${style}</style>${html}`
  }

  set log(entry: LogEntry) {
    const root = this.shadowRoot!
    root.querySelector('.timestamp')!.textContent = entry.timestamp
    root.querySelector('.method')!.textContent = entry.method
    // Log text can contain HTML or commands. Never interpret it as markup.
    root.querySelector('.message')!.textContent = entry.message
    const links = root.querySelector('.report-links')!
    links.replaceChildren()
    for (const link of entry.links ?? []) {
      const anchor = document.createElement('a')
      anchor.textContent = link.label
      anchor.href = link.href
      anchor.target = '_blank'
      anchor.rel = 'noopener noreferrer'
      links.append(anchor)
    }
    this.dataset.status = entry.status
    this.dataset.logId = entry.id
  }
}

if (!customElements.get('adw-log-object')) customElements.define('adw-log-object', LogObject)

if (import.meta.hot) import.meta.hot.accept(() => window.location.reload())
