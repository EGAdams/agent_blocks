/** Shared browser-side shapes for the Voice Communication boundary. */

export type SessionId = string;
export type GenerationId = number;

export interface StartSessionRequest {
  readonly agentId: string;
  readonly conversationId?: string | null;
}

export interface StartSessionResponse {
  readonly sessionId: SessionId;
}

export interface StopSessionRequest {
  readonly sessionId: SessionId;
}

export interface AudioFrame {
  readonly sessionId: SessionId;
  readonly sequence: number;
  readonly pcmBase64: string;
}

export interface TranscriptEvent {
  readonly sessionId: SessionId;
  readonly generation: GenerationId;
  readonly text: string;
  readonly final: boolean;
}

export type AgentEventKind =
  | "reasoning"
  | "assistant_text"
  | "tool"
  | "terminal"
  | "error";

export interface AgentEvent {
  readonly sessionId: SessionId;
  readonly generation: GenerationId;
  readonly kind: AgentEventKind;
  readonly text: string;
}

export interface SpeakRequest {
  readonly sessionId: SessionId;
  readonly generation: GenerationId;
  readonly text: string;
  readonly voice?: string | null;
}

export interface PipelineHandle {
  readonly sessionId: SessionId;
  readonly pipelineId: string;
}

export type VoiceHealthStatus = "starting" | "healthy" | "degraded" | "down";

export interface VoiceHealthEvent {
  readonly component: string;
  readonly status: VoiceHealthStatus;
  readonly detail?: string | null;
  readonly latencyMs?: number | null;
}
