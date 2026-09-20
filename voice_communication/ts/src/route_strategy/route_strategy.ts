/** Strategy for fail-closed routing from transcript to agent name. */
export interface IRouteStrategy {
  route(transcript: string): string | null;
}
