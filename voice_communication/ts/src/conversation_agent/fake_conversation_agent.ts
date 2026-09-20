import type { AgentEvent, AgentEventKind, GenerationId, SessionId } from "../typescript_contracts/contracts.js";
import type { IConversationAgent } from "./conversation_agent.js";

/** One scripted step; text defaults to the submitted turn's own text. */
export interface FakeConversationAgentStep {
  readonly kind: AgentEventKind;
  readonly text?: string;
}

const DEFAULT_SCRIPT: readonly FakeConversationAgentStep[] = [
  { kind: "assistant_text" },
  { kind: "terminal", text: "" },
];

/** Scripted IConversationAgent, needing no network, for driving the contract suite and callers under test. */
export class FakeConversationAgent implements IConversationAgent {
  constructor(
    private readonly sessionId: SessionId = "fake-session",
    private readonly script: readonly FakeConversationAgentStep[] = DEFAULT_SCRIPT,
  ) {}

  async *submit(
    text: string,
    generation: GenerationId,
  ): AsyncIterable<AgentEvent> {
    for ( const step of this.script ) {
      yield {
        sessionId: this.sessionId,
        generation,
        kind: step.kind,
        text: step.text ?? text,
      };
    }
  }
}
