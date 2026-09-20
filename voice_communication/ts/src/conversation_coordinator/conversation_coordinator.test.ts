import { test } from "node:test";
import assert from "node:assert/strict";
import type { AgentEvent, GenerationId } from "../typescript_contracts/contracts.js";
import { FakeConversationAgent } from "../conversation_agent/fake_conversation_agent.js";
import { VoiceSession } from "../voice_session/voice_session.js";
import { SpokenOutputPolicy } from "../spoken_output_policy/spoken_output_policy.js";
import { ConversationCoordinator } from "./conversation_coordinator.js";

test( "ConversationCoordinator: an interim transcript is ignored; a final one runs a turn", async () => {
  const agent = new FakeConversationAgent( "s", [
    { kind: "assistant_text", text: "answer" },
    { kind: "terminal", text: "" },
  ] );
  const spoken: string[] = [];
  const coordinator = new ConversationCoordinator(
    new VoiceSession(),
    agent,
    new SpokenOutputPolicy(),
    ( text ) => spoken.push( text ),
  );

  await coordinator.handleTranscript( { sessionId: "s", generation: 0, text: "hi", final: false } );
  assert.deepEqual( spoken, [] );

  await coordinator.handleTranscript( { sessionId: "s", generation: 0, text: "hi", final: true } );
  assert.deepEqual( spoken, [ "answer" ] );
} );

test( "ConversationCoordinator: interrupt() before the agent responds discards the reply", async () => {
  let capturedGeneration: GenerationId = -1;
  const agent = {
    async *submit( text: string, generation: GenerationId ): AsyncIterable<AgentEvent> {
      capturedGeneration = generation;
      yield { sessionId: "s", generation, kind: "assistant_text", text: "late" };
      yield { sessionId: "s", generation, kind: "terminal", text: "" };
    },
  };
  const session = new VoiceSession();
  const spoken: string[] = [];
  const coordinator = new ConversationCoordinator( session, agent, new SpokenOutputPolicy(), ( text ) => spoken.push( text ) );

  const turn = coordinator.handleTranscript( { sessionId: "s", generation: 0, text: "hi", final: true } );
  coordinator.interrupt();
  await turn;

  assert.notEqual( capturedGeneration, -1 );
  assert.deepEqual( spoken, [] );
} );
