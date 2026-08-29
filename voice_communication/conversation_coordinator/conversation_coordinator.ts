import type { TranscriptEvent } from "../typescript_contracts/contracts.js";

/** Application port coordinating turns without importing Pipecat or Letta. */
export interface IConversationCoordinator {
  handleTranscript(event: TranscriptEvent): Promise<void>;
  interrupt(): void;
}
