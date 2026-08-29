import type {
  AudioFrame,
  StartSessionRequest,
  StartSessionResponse,
  StopSessionRequest,
  VoiceHealthEvent,
} from "../typescript_contracts/contracts.js";

/** Adapter port between Toyota's application and the separate Pipecat process. */
export interface IPipecatServiceClient {
  startSession(request: StartSessionRequest): Promise<StartSessionResponse>;
  sendAudio(frame: AudioFrame): Promise<void>;
  stopSession(request: StopSessionRequest): Promise<void>;
  health(): Promise<VoiceHealthEvent>;
}
