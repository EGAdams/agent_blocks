import type { AgentEvent, GenerationId } from "../typescript_contracts/contracts.js";

/** Dependency-inverted port through which voice policy talks to an agent. */
export interface IConversationAgent {
  submit(
    text: string,
    generation: GenerationId,
  ): AsyncIterable<AgentEvent>;
}
