import type { AgentEvent, GenerationId, SessionId } from "../typescript_contracts/contracts.js";
import type { IConversationAgent } from "../conversation_agent/conversation_agent.js";

/** The 900s server budget, plus headroom, per docs/overview.md. */
const TURN_TIMEOUT_MS = 900_000;

interface LettaMessageResponse {
  readonly ok: boolean;
  readonly reply?: string;
  readonly error?: string;
}

/**
 * Adapter role for Letta.
 *
 * POSTs one turn to /api/letta-code-message and gets one lump back, so
 * submit() yields a single assistant_text event then a terminal, with
 * ok: false becoming an error event instead. cancel() means the response
 * will not be delivered — the Letta run itself keeps going, since the
 * endpoint offers no server-side cancel.
 */
export class LettaAgentAdapter implements IConversationAgent {
  private readonly cancelled = new Set<GenerationId>();

  constructor(
    private readonly sessionId: SessionId,
    private readonly endpoint: string = "/api/letta-code-message",
    private readonly fetchImpl: typeof fetch = fetch,
  ) {}

  cancel( generation: GenerationId ): void {
    this.cancelled.add( generation );
  }

  async *submit(
    text: string,
    generation: GenerationId,
  ): AsyncIterable<AgentEvent> {
    const controller = new AbortController();
    const timeout = setTimeout( () => controller.abort(), TURN_TIMEOUT_MS );

    try {
      const response = await this.fetchImpl( this.endpoint, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify( { text } ),
        signal: controller.signal,
      } );
      const body = ( await response.json() ) as LettaMessageResponse;

      if ( this.cancelled.has( generation ) ) {
        return;
      }

      if ( !body.ok ) {
        yield {
          sessionId: this.sessionId,
          generation,
          kind: "error",
          text: body.error ?? "letta agent call failed",
        };
        return;
      }

      yield {
        sessionId: this.sessionId,
        generation,
        kind: "assistant_text",
        text: body.reply ?? "",
      };
      yield {
        sessionId: this.sessionId,
        generation,
        kind: "terminal",
        text: "",
      };
    } finally {
      clearTimeout( timeout );
      this.cancelled.delete( generation );
    }
  }
}
