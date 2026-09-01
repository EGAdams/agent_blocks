/** Public TypeScript interface catalog for the Voice Communication blocks. */

export type { IConversationAgent } from "../conversation_agent/conversation_agent.js";
export type { IConversationCoordinator } from "../conversation_coordinator/conversation_coordinator.js";
export type {
  AgentEvent,
  AgentEventKind,
  AudioFrame,
  GenerationId,
  PipelineHandle,
  SessionId,
  SpeakRequest,
  StartSessionRequest,
  StartSessionResponse,
  StopSessionRequest,
  TranscriptEvent,
  VoiceHealthEvent,
  VoiceHealthStatus,
} from "../typescript_contracts/contracts.js";
export type { IVoiceDesignRule } from "../design_protocol/design_protocol.js";
export type { IInputDraft } from "../input_draft/input_draft.js";
export type { ILanguageProcessor } from "../language_processor/language_processor.js";
export type { ILettaAgentAdapter } from "../letta_agent_adapter/letta_agent_adapter.js";
export type { INoteCommandChannel } from "../note_command_channel/note_command_channel.js";
export type { IPipecatServiceClient } from "../pipecat_service_client/pipecat_service_client.js";
export type { IRouteStrategy } from "../route_strategy/route_strategy.js";
export type { ISpokenOutputPolicy } from "../spoken_output_policy/spoken_output_policy.js";
export type { IToyotaVoiceApplication } from "../toyota_voice_application/toyota_voice_application.js";
export type { IVoiceHealthObserver } from "../voice_health_observer/voice_health_observer.js";
export type { IVoiceSession } from "../voice_session/voice_session.js";
