/** One known report route, shared by the server and terminal event adapter. */
export function remainingWorkUrl(adwId: string): string | null {
  if (!/^[A-Za-z0-9._-]+$/.test(adwId) || adwId === '.' || adwId === '..') return null
  return `/api/sessions/${encodeURIComponent(adwId)}/reports/remaining-work.html`
}

/** Markdown stays on disk; its browser representation is HTML. */
export function documentUrl(path: string, adwId: string): string {
  return `/api/documents/view.html?path=${encodeURIComponent(path)}&session=${encodeURIComponent(adwId)}`
}

export function markdownPaths(text: string): string[] {
  const found = new Set<string>()
  // Quoted paths can contain spaces; unquoted paths end at a whitespace boundary.
  const pattern = /`([^`\n]+\.md)`|"([^"\n]+\.md)"|'([^'\n]+\.md)'|([^\s`"'<>()[\]{},;]+\.md)(?=$|[\s`"'<>()[\]{},;:.])/g
  for (const match of text.matchAll(pattern)) {
    const path = match[1] ?? match[2] ?? match[3] ?? match[4]!
    if (!/^[a-z][a-z0-9+.-]*:/i.test(path) && !path.startsWith('//') && !/[\r\n\0]/.test(path)) found.add(path)
  }
  return [...found]
}
