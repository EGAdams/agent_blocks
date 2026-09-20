import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { HttpPipecatServiceClient, parseVoiceHealthEvent } from "./http_pipecat_service_client.js";

const FIXTURE_PATH = fileURLToPath( new URL( "../../../contracts/fixtures/voice_health_event.json", import.meta.url ) );

test( "pipecat_service_client: TS accepts the same wire fixture Python does", async () => {
  const raw = await readFile( FIXTURE_PATH, "utf8" );
  const event = parseVoiceHealthEvent( JSON.parse( raw ) );

  assert.equal( event.component, "pipecat_local_service" );
  assert.equal( event.status, "healthy" );
  assert.equal( event.latencyMs, 12.5 );
} );

test( "HttpPipecatServiceClient: health() parses the fixture through the full adapter path", async () => {
  const raw = await readFile( FIXTURE_PATH, "utf8" );
  const stubFetch = ( async () => ( { json: async () => JSON.parse( raw ) } ) ) as unknown as typeof fetch;
  const client = new HttpPipecatServiceClient( "http://localhost:9000", stubFetch );

  const event = await client.health();

  assert.equal( event.status, "healthy" );
} );

test( "parseVoiceHealthEvent: fails closed on an unrecognized status", () => {
  assert.throws( () => parseVoiceHealthEvent( { component: "x", status: "on-fire" } ) );
} );
