import type {
  AudioFrame,
  StartSessionRequest,
  StartSessionResponse,
  StopSessionRequest,
  VoiceHealthEvent,
  VoiceHealthStatus,
} from "../typescript_contracts/contracts.js";
import type { IPipecatServiceClient } from "./pipecat_service_client.js";

const VOICE_HEALTH_STATUSES: ReadonlySet<string> = new Set( [ "starting", "healthy", "degraded", "down" ] );

/** Fails closed on a malformed payload rather than guessing a status, same as LettaAgentAdapter's parsing. */
export function parseVoiceHealthEvent( raw: unknown ): VoiceHealthEvent {
  if ( typeof raw !== "object" || raw === null ) {
    throw new Error( "parseVoiceHealthEvent: malformed VoiceHealthEvent payload" );
  }
  const { component, status, detail, latencyMs } = raw as Record<string, unknown>;
  if ( typeof component !== "string" || typeof status !== "string" || !VOICE_HEALTH_STATUSES.has( status ) ) {
    throw new Error( "parseVoiceHealthEvent: malformed VoiceHealthEvent payload" );
  }
  return {
    component,
    status: status as VoiceHealthStatus,
    detail: typeof detail === "string" ? detail : null,
    latencyMs: typeof latencyMs === "number" ? latencyMs : null,
  };
}

/**
 * Adapter role for the separate Pipecat process: the browser side of the
 * wire IPipecatServiceClient describes. fetch is constructor-injected, same
 * pattern as LettaAgentAdapter, so no test needs a real server.
 */
export class HttpPipecatServiceClient implements IPipecatServiceClient {
  constructor(
    private readonly baseUrl: string,
    private readonly fetchImpl: typeof fetch = fetch,
  ) {}

  async startSession( request: StartSessionRequest ): Promise<StartSessionResponse> {
    const response = await this.fetchImpl( `${ this.baseUrl }/session`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify( request ),
    } );
    return ( await response.json() ) as StartSessionResponse;
  }

  async sendAudio( frame: AudioFrame ): Promise<void> {
    await this.fetchImpl( `${ this.baseUrl }/audio`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify( frame ),
    } );
  }

  async stopSession( request: StopSessionRequest ): Promise<void> {
    await this.fetchImpl( `${ this.baseUrl }/session/stop`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify( request ),
    } );
  }

  async health(): Promise<VoiceHealthEvent> {
    const response = await this.fetchImpl( `${ this.baseUrl }/health`, { method: "GET" } );
    return parseVoiceHealthEvent( await response.json() );
  }
}
