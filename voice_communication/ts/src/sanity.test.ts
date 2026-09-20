/** Trivial smoke test proving the node:test runner is wired up end to end. */
import { test } from "node:test";
import assert from "node:assert/strict";

test( "sanity: the test runner executes and can assert", () => {
  assert.equal( 1 + 1, 2 );
});
