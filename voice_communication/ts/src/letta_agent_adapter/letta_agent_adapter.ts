import type { AgentEvent, GenerationId } from "../typescript_contracts/contracts.js";
import type { IConversationAgent } from "../conversation_agent/conversation_agent.js";

/**
 * Adapter role for Letta.
 *
 * Not yet implemented; callers depend only on IConversationAgent, the
 * same pattern ClaudeAgentAdapter already proves in Python.
 */
export class LettaAgentAdapter implements IConversationAgent {
  submit(
    _text: string,
    _generation: GenerationId,
  ): AsyncIterable<AgentEvent> {
    throw new Error("LettaAgentAdapter.submit is not implemented yet");
  }
}
