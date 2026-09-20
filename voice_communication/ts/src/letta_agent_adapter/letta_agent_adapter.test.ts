import { test } from "node:test";
import assert from "node:assert/strict";
import { runConversationAgentContract } from "../conversation_agent/conversation_agent.contract.js";
import { LettaAgentAdapter } from "./letta_agent_adapter.js";

function stubFetch( body: unknown ): typeof fetch {
  return ( async () => ( { json: async () => body } ) ) as unknown as typeof fetch;
}

runConversationAgentContract(
  "LettaAgentAdapter",
  () => new LettaAgentAdapter( "fake-session", "/api/letta-code-message", stubFetch( { ok: true, reply: "hello" } ) ),
);

test( "LettaAgentAdapter: ok:false becomes an error event instead of assistant_text", async () => {
  const adapter = new LettaAgentAdapter(
    "fake-session",
    "/api/letta-code-message",
    stubFetch( { ok: false, error: "boom" } ),
  );
  const events = [];
  for await ( const event of adapter.submit( "hi", 1 ) ) {
    events.push( event );
  }
  assert.equal( events.length, 1 );
  assert.equal( events[ 0 ]?.kind, "error" );
  assert.equal( events[ 0 ]?.text, "boom" );
} );

test( "LettaAgentAdapter: cancel() before the response arrives suppresses delivery entirely", async () => {
  const adapter = new LettaAgentAdapter(
    "fake-session",
    "/api/letta-code-message",
    stubFetch( { ok: true, reply: "too late" } ),
  );
  adapter.cancel( 3 );
  const events = [];
  for await ( const event of adapter.submit( "hi", 3 ) ) {
    events.push( event );
  }
  assert.deepEqual( events, [] );
} );
