/** Display data only; the terminal never fetches or changes workflow state. */
export interface LogEntry {
  id: string
  timestamp: string
  method: string
  message: string
  status: 'info' | 'success' | 'error'
}
