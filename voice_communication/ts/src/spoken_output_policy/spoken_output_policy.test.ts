import { test } from "node:test";
import assert from "node:assert/strict";
import type { AgentEvent } from "../typescript_contracts/contracts.js";
import { SpokenOutputPolicy } from "./spoken_output_policy.js";

function event( generation: number, kind: AgentEvent[ "kind" ], text = "text" ): AgentEvent {
  return { sessionId: "s", generation, kind, text };
}

test( "SpokenOutputPolicy: an assistant_text from a superseded generation is dropped", () => {
  const policy = new SpokenOutputPolicy();
  const events = [ event( 1, "assistant_text", "March answer" ) ];

  assert.deepEqual( policy.select( events, 2 ), [] );
} );

test( "SpokenOutputPolicy: a reasoning event from the current generation is dropped too", () => {
  const policy = new SpokenOutputPolicy();
  const events = [ event( 2, "reasoning", "thinking..." ) ];

  assert.deepEqual( policy.select( events, 2 ), [] );
} );

test( "SpokenOutputPolicy: an assistant_text from the current generation is admitted", () => {
  const policy = new SpokenOutputPolicy();
  const target = event( 2, "assistant_text", "April answer" );
  const events = [ event( 1, "assistant_text", "March answer" ), target ];

  assert.deepEqual( policy.select( events, 2 ), [ target ] );
} );
