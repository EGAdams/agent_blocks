import { test } from "node:test";
import assert from "node:assert/strict";
import type { AgentEvent, GenerationId } from "../typescript_contracts/contracts.js";
import type { IConversationAgent } from "./conversation_agent.js";

/** Shared Liskov suite for every IConversationAgent implementation: run it against each adapter, including fakes. */
export function runConversationAgentContract(
  label: string,
  makeAgent: () => IConversationAgent,
): void {
  test( `${ label }: submit() ends with exactly one terminal or error event, and it is last`, async () => {
    const agent = makeAgent();
    const generation: GenerationId = 1;
    const events: AgentEvent[] = [];
    for await ( const event of agent.submit( "hello", generation ) ) {
      events.push( event );
    }

    assert.ok( events.length > 0 );
    const endingEvents = events.filter( ( event ) => event.kind === "terminal" || event.kind === "error" );
    assert.equal( endingEvents.length, 1 );
    const last = events[ events.length - 1 ];
    assert.ok( last !== undefined && ( last.kind === "terminal" || last.kind === "error" ) );
  } );

  test( `${ label }: every event carries the generation it was submitted with`, async () => {
    const agent = makeAgent();
    const generation: GenerationId = 7;
    for await ( const event of agent.submit( "what did we spend in March?", generation ) ) {
      assert.equal( event.generation, generation );
    }
  } );
}
