import { test } from "node:test";
import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import { VoiceDesignRule } from "./design_protocol.js";

// process.cwd() is ts/dist-test at test-run time (npm test cd's there before
// running), so the real TypeScript source always sits one level up in src/ —
// scanning dist-test's own compiled .js would miss the ".ts" filter below.
const SRC_ROOT = `${ process.cwd() }/../src/`;

const RESTRICTED_PATTERNS: ReadonlyArray<readonly [ RegExp, string ]> = [
  [ /\bfetch\b/, "fetch" ],
  [ /\bAbortController\b/, "AbortController" ],
];

async function sourceFiles(): Promise<string[]> {
  const entries = await readdir( SRC_ROOT, { recursive: true } );
  return entries.filter(
    ( entry ) =>
      entry.endsWith( ".ts" ) &&
      !entry.endsWith( ".d.ts" ) &&
      !entry.endsWith( ".test.ts" ) &&
      !entry.endsWith( ".contract.ts" ) &&
      // The rule's own definition names its restricted targets as string
      // literals ("fetch"), which is not a usage the rule needs to police.
      !entry.startsWith( "design_protocol/" ),
  );
}

test( "VoiceDesignRule: rejects a non-adapter module depending on a restricted target", () => {
  const rule = new VoiceDesignRule();

  assert.equal( rule.acceptsDependency( "conversation_agent", "fetch" ), false );
  assert.equal( rule.acceptsDependency( "letta_agent_adapter", "fetch" ), true );
  assert.equal( rule.acceptsDependency( "http_pipecat_service_client", "AbortController" ), true );
  assert.equal( rule.acceptsDependency( "conversation_agent", "typescript_contracts" ), true );
} );

test( "design protocol fitness: only a registered adapter's own file may reference fetch/AbortController", async () => {
  const rule = new VoiceDesignRule();
  const files = await sourceFiles();
  let sawARestrictedUsage = false;

  for ( const relativePath of files ) {
    const content = await readFile( `${ SRC_ROOT }${ relativePath }`, "utf8" );
    const sourceModule = relativePath.split( /[\\/]/ ).pop()?.replace( /\.ts$/, "" ) ?? relativePath;

    for ( const [ pattern, target ] of RESTRICTED_PATTERNS ) {
      if ( pattern.test( content ) ) {
        sawARestrictedUsage = true;
        assert.ok(
          rule.acceptsDependency( sourceModule, target ),
          `${ relativePath } references ${ target } but is not in KNOWN_TRANSPORT_ADAPTERS`,
        );
      }
    }
  }

  assert.ok( sawARestrictedUsage, "expected at least one real restricted usage in the codebase to check against" );
} );
