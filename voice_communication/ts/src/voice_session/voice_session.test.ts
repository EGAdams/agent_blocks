import { test } from "node:test";
import assert from "node:assert/strict";
import { VoiceSession } from "./voice_session.js";

test( "VoiceSession: interrupting turn A orphans it before turn B begins", () => {
  const session = new VoiceSession();
  const genA = session.beginTurn();
  session.interrupt();
  const genB = session.beginTurn();

  assert.equal( session.accepts( genA ), false );
  assert.equal( session.accepts( genB ), true );
} );

test( "VoiceSession: accepts() fails closed on a generation it never minted", () => {
  const session = new VoiceSession();
  session.beginTurn();

  assert.equal( session.accepts( 999 ), false );
} );

test( "VoiceSession: a closed session accepts nothing, even its own live generation", () => {
  const session = new VoiceSession();
  const gen = session.beginTurn();
  session.close();

  assert.equal( session.accepts( gen ), false );
} );

test( "VoiceSession: beginTurn() after close() throws", () => {
  const session = new VoiceSession();
  session.close();

  assert.throws( () => session.beginTurn() );
} );
