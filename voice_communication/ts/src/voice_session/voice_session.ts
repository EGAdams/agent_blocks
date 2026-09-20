import type { GenerationId } from "../typescript_contracts/contracts.js";

/** Framework-neutral lifecycle port for one voice conversation. */
export interface IVoiceSession {
  beginTurn(): GenerationId;
  accepts(generation: GenerationId): boolean;
  interrupt(): void;
  close(): void;
}
