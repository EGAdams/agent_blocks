import type { AgentEvent, GenerationId } from "../typescript_contracts/contracts.js";

/** Strategy deciding which current agent events may be spoken. */
export interface ISpokenOutputPolicy {
  select(
    events: readonly AgentEvent[],
    generation: GenerationId,
  ): readonly AgentEvent[];
}
