import { test } from "node:test";
import assert from "node:assert/strict";
import { FakeConversationAgent } from "../conversation_agent/fake_conversation_agent.js";
import { VoiceSession } from "../voice_session/voice_session.js";
import { SpokenOutputPolicy } from "../spoken_output_policy/spoken_output_policy.js";
import { ToyotaVoiceApplication } from "./toyota_voice_application.js";

test( "ToyotaVoiceApplication: a full turn speaks exactly the assistant_text reply", async () => {
  const agent = new FakeConversationAgent( "fake-session", [
    { kind: "reasoning", text: "thinking..." },
    { kind: "assistant_text", text: "the April total was $412" },
    { kind: "terminal", text: "" },
  ] );
  const spoken: string[] = [];
  const app = new ToyotaVoiceApplication(
    agent,
    new VoiceSession(),
    new SpokenOutputPolicy(),
    ( text ) => spoken.push( text ),
  );

  await app.runTurn( "what did we spend in April?" );

  assert.deepEqual( spoken, [ "the April total was $412" ] );
} );
