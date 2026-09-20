import type { VoiceHealthEvent } from "../typescript_contracts/contracts.js";

/** Observer port for health, latency, interruption, and fallback events. */
export interface IVoiceHealthObserver {
  onEvent(event: VoiceHealthEvent): void;
}
