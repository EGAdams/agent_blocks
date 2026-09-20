import type { AgentEvent, AgentEventKind, GenerationId } from "../typescript_contracts/contracts.js";

/** Strategy deciding which current agent events may be spoken. */
export interface ISpokenOutputPolicy {
  select(
    events: readonly AgentEvent[],
    generation: GenerationId,
  ): readonly AgentEvent[];
}

const SPEAKABLE_KINDS: ReadonlySet<AgentEventKind> = new Set( [ "assistant_text" ] );

/**
 * The single gate between agent output and a speaker: an event reaches it
 * only if its generation is still the current one AND its kind is
 * speakable. Order does not matter here since both checks are per-event,
 * unlike a stateful fence that must check supersession first.
 */
export class SpokenOutputPolicy implements ISpokenOutputPolicy {
  select(
    events: readonly AgentEvent[],
    generation: GenerationId,
  ): readonly AgentEvent[] {
    return events.filter(
      ( event ) => event.generation === generation && SPEAKABLE_KINDS.has( event.kind ),
    );
  }
}
