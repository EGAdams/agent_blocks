// Only the viewer adds this script; source documents and log text stay literal.
const button = document.getElementById('refresh-totals')
const status = document.getElementById('refresh-totals-status')
const match = window.location.pathname.match(/^\/api\/sessions\/([A-Za-z0-9._-]+)\/reports\/remaining-work\.html$/)

if (button && status && match) {
  button.addEventListener('click', async () => {
    if (button.disabled) return
    button.disabled = true
    button.textContent = 'Refreshing…'
    status.textContent = 'Checking current records…'
    try {
      const response = await fetch(`/api/sessions/${match[1]}/reports/refresh`, {
        method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}',
      })
      const result = await response.json()
      if (!response.ok) throw new Error(result.error || 'The status check could not finish.')
      const expected = `/api/sessions/${encodeURIComponent(result.adw_id)}/reports/remaining-work.html`
      if (typeof result.adw_id !== 'string' || !/^[A-Za-z0-9_-]+$/.test(result.adw_id)
          || result.report_url !== expected) throw new Error('The server returned an invalid report link.')
      window.location.replace(expected + '#expense-summary')
    } catch (error) {
      status.textContent = `Refresh failed. ${error.message} The displayed totals are unchanged.`
      button.disabled = false
      button.textContent = 'Refresh Totals'
    }
  })
}
