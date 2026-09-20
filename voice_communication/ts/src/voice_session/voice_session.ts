import type { GenerationId } from "../typescript_contracts/contracts.js";

/** Framework-neutral lifecycle port for one voice conversation. */
export interface IVoiceSession {
  beginTurn(): GenerationId;
  accepts(generation: GenerationId): boolean;
  interrupt(): void;
  close(): void;
}

const NO_GENERATION: GenerationId = 0;

/**
 * The fence. beginTurn() mints a monotonically increasing generation that
 * supersedes any live turn; accepts() is true only for the current one, so
 * an interrupted turn's late events are rejected forever, not just delayed.
 */
export class VoiceSession implements IVoiceSession {
  private currentGeneration: GenerationId = NO_GENERATION;
  private nextGeneration: GenerationId = 1;
  private closed = false;

  beginTurn(): GenerationId {
    if ( this.closed ) {
      throw new Error( "VoiceSession.beginTurn: session is closed" );
    }
    this.currentGeneration = this.nextGeneration;
    this.nextGeneration += 1;
    return this.currentGeneration;
  }

  accepts( generation: GenerationId ): boolean {
    return !this.closed && generation !== NO_GENERATION && generation === this.currentGeneration;
  }

  interrupt(): void {
    this.currentGeneration = NO_GENERATION;
  }

  close(): void {
    this.closed = true;
    this.currentGeneration = NO_GENERATION;
  }
}
