import type { AgentEvent, TranscriptEvent } from "../typescript_contracts/contracts.js";
import type { IConversationAgent } from "../conversation_agent/conversation_agent.js";
import type { IVoiceSession } from "../voice_session/voice_session.js";
import type { ISpokenOutputPolicy } from "../spoken_output_policy/spoken_output_policy.js";
import type { Speaker } from "../toyota_voice_application/toyota_voice_application.js";

/** Application port coordinating turns without importing Pipecat or Letta. */
export interface IConversationCoordinator {
  handleTranscript(event: TranscriptEvent): Promise<void>;
  interrupt(): void;
}

/**
 * The Mediator between one finalized transcript and one agent run: an
 * interim TranscriptEvent is ignored, a final one begins a turn, and the
 * fence is re-checked after the agent responds — not just before — so an
 * interrupt() that lands mid-turn still stops the reply from being spoken.
 */
export class ConversationCoordinator implements IConversationCoordinator {
  constructor(
    private readonly session: IVoiceSession,
    private readonly agent: IConversationAgent,
    private readonly policy: ISpokenOutputPolicy,
    private readonly speak: Speaker,
  ) {}

  interrupt(): void {
    this.session.interrupt();
  }

  close(): void {
    this.session.close();
  }

  async handleTranscript( event: TranscriptEvent ): Promise<void> {
    if ( !event.final ) {
      return;
    }

    const generation = this.session.beginTurn();
    const collected: AgentEvent[] = [];
    for await ( const agentEvent of this.agent.submit( event.text, generation ) ) {
      collected.push( agentEvent );
    }

    if ( !this.session.accepts( generation ) ) {
      return;
    }
    for ( const spoken of this.policy.select( collected, generation ) ) {
      this.speak( spoken.text );
    }
  }
}
