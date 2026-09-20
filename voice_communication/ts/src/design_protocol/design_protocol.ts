/** Architectural fitness rule for the Voice Communication dependency graph. */
export interface IVoiceDesignRule {
  acceptsDependency(sourceModule: string, targetModule: string): boolean;
}

/** Targets that count as "an SDK, a transport, or a DOM API" for this rule. */
const RESTRICTED_TARGETS: ReadonlySet<string> = new Set( [ "fetch", "AbortController" ] );

/**
 * Explicit registry of modules allowed to depend on a restricted target.
 * Adding a new transport-touching adapter means adding its module name here
 * in the same edit — the point of a fitness test is that this list is the
 * only place the exception can be granted.
 */
const KNOWN_TRANSPORT_ADAPTERS: ReadonlySet<string> = new Set( [
  "letta_agent_adapter",
  "http_pipecat_service_client",
] );

/** Enforces "an adapter is the only file allowed to import an SDK, a transport, or a DOM API." */
export class VoiceDesignRule implements IVoiceDesignRule {
  acceptsDependency( sourceModule: string, targetModule: string ): boolean {
    if ( !RESTRICTED_TARGETS.has( targetModule ) ) {
      return true;
    }
    return KNOWN_TRANSPORT_ADAPTERS.has( sourceModule );
  }
}
