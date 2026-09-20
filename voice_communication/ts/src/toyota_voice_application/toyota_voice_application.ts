import type { IConversationAgent } from "../conversation_agent/conversation_agent.js";
import type { IVoiceSession } from "../voice_session/voice_session.js";
import type { ISpokenOutputPolicy } from "../spoken_output_policy/spoken_output_policy.js";
import { ConversationCoordinator } from "../conversation_coordinator/conversation_coordinator.js";

/** Facade composed at the Toyota/dashboard boundary. */
export interface IToyotaVoiceApplication {
  start(): Promise<void>;
  stop(): Promise<void>;
}

/** Sink for text the policy has cleared to speak; a real implementation posts to Pipecat. */
export type Speaker = ( text: string ) => void;

/**
 * The composition root. Its constructor is the first and only place naming
 * a concrete IConversationAgent, IVoiceSession, and ISpokenOutputPolicy
 * together — it hands them straight to a ConversationCoordinator, which owns
 * the turn logic itself; every other file here depends only on the ports.
 */
export class ToyotaVoiceApplication implements IToyotaVoiceApplication {
  private readonly coordinator: ConversationCoordinator;

  constructor(
    agent: IConversationAgent,
    session: IVoiceSession,
    policy: ISpokenOutputPolicy,
    speak: Speaker,
  ) {
    this.coordinator = new ConversationCoordinator( session, agent, policy, speak );
  }

  async start(): Promise<void> {}

  async stop(): Promise<void> {
    this.coordinator.close();
  }

  /** Test/composition-root convenience: wraps text as a final TranscriptEvent for the coordinator. */
  async runTurn( text: string ): Promise<void> {
    await this.coordinator.handleTranscript( {
      sessionId: "toyota-session",
      generation: 0,
      text,
      final: true,
    } );
  }
}
