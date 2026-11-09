<?php
declare(strict_types=1);
namespace AgentBridge\Tests;

/** A gateway with the Notes agent, implement on, and no waiting (tests drive the worker by hand). */
function notesWorld(array $overrides = []): World
{
    return new World($overrides + [
        'allowed_operations' => ['inspect', 'explain', 'search_memory', 'implement'],
        'allowed_modes'      => ['read_only', 'workspace_write'],
        'notes_wait_seconds' => 0,
        'agents' => [
            ['name' => 'codex', 'display_name' => 'Codex', 'description' => 'reads code',
             'operations' => ['inspect', 'explain', 'search_memory'], 'workspaces' => ['agent_blocks']],
            ['name' => 'notes', 'display_name' => 'Notes', 'description' => 'EG\'s notes',
             'operations' => ['inspect', 'implement'], 'workspaces' => ['notes'], 'write_workspaces' => ['notes']],
        ],
    ]);
}

function notesCall(World $w, string $tool, array $args = [], ?string $token = null): array
{
    return $w->mcp('tools/call', ['name' => $tool, 'arguments' => $args ?: new \stdClass()], 1, $token)->json['result'];
}

const NOTES_NOTE      = '2026-11-05_synthetic-deed.md';
const NOTES_ITEM      = 'r-0123456789';
const NOTES_REVISION  = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
const NOTES_READS  = ['notes_inspect', 'notes_get_active_highlight', 'notes_verify', 'notes_snapshots', 'notes_watch',
                      'notes_get_result'];
const NOTES_WRITES = ['notes_stop_blink', 'notes_activate', 'notes_activate_next', 'notes_edit', 'notes_mark_resolved',
                      'notes_rollback'];

test('notes: every notes_* tool is listed when the agent exists and implement is on', function () {
    $names = toolNames(notesWorld());
    foreach (array_merge(NOTES_READS, NOTES_WRITES) as $tool) {
        ok(in_array($tool, $names, true), "$tool listed");
    }
    ok(in_array('submit_agent_task', $names, true), 'task tools still listed');
});

test('notes: none without the agent, and only reads without implement', function () {
    $none = toolNames(new World());
    ok(!array_filter($none, function ($n) { return strpos($n, 'notes_') === 0; }), 'none without the agent');
    $w     = notesWorld(['allowed_operations' => ['inspect', 'explain', 'search_memory'], 'allowed_modes' => ['read_only']]);
    $names = toolNames($w);
    foreach (NOTES_READS as $tool) {
        ok(in_array($tool, $names, true), "$tool listed read-only");
    }
    foreach (NOTES_WRITES as $tool) {
        ok(!in_array($tool, $names, true), "$tool hidden read-only");
    }
    eq(true, notesCall($w, 'notes_edit', ['note' => NOTES_NOTE, 'item_id' => NOTES_ITEM, 'replace' => 'x',
                                          'expected_revision' => NOTES_REVISION])['isError']);
});

test('notes: a client scope without the notes workspace hides the tools and refuses the call', function () {
    $id = 'k_' . str_pad('2', 24, '0', STR_PAD_LEFT);
    $w  = notesWorld(['client_scopes' => [$id => ['sender' => 'toyota-bridge', 'operations' => ['inspect'],
                                                   'workspaces' => ['agent_blocks']]]]);
    ok(!in_array('notes_inspect', toolNames($w), true), 'hidden from the scoped client');
    eq(true, notesCall($w, 'notes_inspect', ['note' => NOTES_NOTE])['isError']);
    ok(in_array('notes_inspect', toolNames($w, $w->client2), true), 'unscoped client still sees it');
});

test('notes: inspect queues one read-only task with a gateway-written prompt', function () {
    $w   = notesWorld();
    $out = notesCall($w, 'notes_inspect', ['note' => NOTES_NOTE, 'css' => '#not-a-notes-field']);
    eq(false, $out['isError']);
    $s = $out['structuredContent'];
    eq(false, $s['ready']);
    ok(strpos($s['next'], 'notes_get_result') !== false, 'tells the caller what to do next');
    list($prompt) = claimTask($w, $s['task_id']);
    eq(['notes' => 'review_inspect', 'id' => NOTES_NOTE], json_decode($prompt, true), 'unknown caller fields are dropped');
    $task = $w->call('GET', '/tasks/' . $s['task_id'], $w->client)->json;
    eq('notes', $task['agent']);
    eq('inspect', $task['operation']);
    eq('read_only', $task['approval_mode']);
});

test('notes: an edit carries the item, the words and the revision, and runs as a write', function () {
    $w  = notesWorld();
    $id = notesCall($w, 'notes_edit', ['note' => NOTES_NOTE, 'item_id' => NOTES_ITEM, 'replace' => '[VERIFY: plat page 31]',
                                       'expected_revision' => NOTES_REVISION, 'page' => true])['structuredContent']['task_id'];
    list($prompt) = claimTask($w, $id);
    eq(['notes' => 'review_edit', 'id' => NOTES_NOTE, 'item_id' => NOTES_ITEM, 'replace' => '[VERIFY: plat page 31]',
        'expected_revision' => NOTES_REVISION, 'page' => true], json_decode($prompt, true));
    $task = $w->call('GET', "/tasks/$id", $w->client)->json;
    eq('implement', $task['operation']);
    eq('workspace_write', $task['approval_mode']);
});

test('notes: incomplete calls are refused before any task is queued', function () {
    $w = notesWorld();
    eq(true, notesCall($w, 'notes_inspect')['isError'], 'a note is always needed');
    eq(true, notesCall($w, 'notes_edit', ['note' => NOTES_NOTE, 'item_id' => NOTES_ITEM, 'replace' => 'x'])['isError'],
       'an edit needs the revision it was read at');
    $resolve = notesCall($w, 'notes_mark_resolved', ['note' => NOTES_NOTE, 'item_id' => NOTES_ITEM, 'expected_revision' => NOTES_REVISION]);
    eq(true, $resolve['isError'], 'resolving needs evidence or approval');
    ok(strpos($resolve['content'][0]['text'], 'evidence') !== false, 'and says so');
    eq(true, notesCall($w, 'notes_watch', ['note' => NOTES_NOTE])['isError'], 'watch needs until');
    eq(true, notesCall($w, 'notes_watch', ['note' => NOTES_NOTE, 'until' => 'forever'])['isError'], 'until is one of three');
    eq(true, notesCall($w, 'notes_edit', ['note' => NOTES_NOTE, 'item_id' => NOTES_ITEM, 'replace' => str_repeat('x', 2001),
                                          'expected_revision' => NOTES_REVISION])['isError'], 'a book is refused');
    eq(true, notesCall($w, 'notes_rollback', ['note' => NOTES_NOTE, 'expected_revision' => NOTES_REVISION])['isError'],
       'rollback needs the snapshot');
});

test('notes: resolving with evidence or approval queues it, advance as a switch', function () {
    $w  = notesWorld();
    $id = notesCall($w, 'notes_mark_resolved', ['note' => NOTES_NOTE, 'item_id' => NOTES_ITEM, 'expected_revision' => NOTES_REVISION,
                                                'approval' => 'EG said that one is settled', 'advance' => true,
                                                'chain' => true])['structuredContent']['task_id'];
    list($prompt) = claimTask($w, $id);
    eq(['notes' => 'review_mark_resolved', 'id' => NOTES_NOTE, 'item_id' => NOTES_ITEM, 'expected_revision' => NOTES_REVISION,
        'approval' => 'EG said that one is settled', 'advance' => true], json_decode($prompt, true),
       'a field this tool does not take (chain) is dropped');
});

test('notes: a finished answer is verified on the worker\'s ok, with the blinking text and the adw_id', function () {
    $w  = notesWorld();
    $id = notesCall($w, 'notes_inspect', ['note' => NOTES_NOTE])['structuredContent']['task_id'];
    list(, $lease) = claimTask($w, $id);
    completeTask($w, $id, $lease, ['notes' => 'review_inspect', 'id' => NOTES_NOTE, 'ok' => true, 'note' => NOTES_NOTE,
                                   'revision' => NOTES_REVISION, 'active_item_id' => NOTES_ITEM, 'active_line' => 11,
                                   'active_text' => '[VERIFY: plat page]', 'highlight_status' => 'blinking',
                                   'pending_count' => 4, 'adw_id' => 'abcd1234', 'token' => 'never-copied']);
    $s = notesCall($w, 'notes_get_result', ['task_id' => $id])['structuredContent'];
    eq(true, $s['ready']);
    eq(true, $s['verified']);
    eq('[VERIFY: plat page]', $s['active_text']);
    eq(NOTES_ITEM, $s['active_item_id']);
    eq('abcd1234', $s['adw_id']);
    ok(!isset($s['token']), 'only known detail keys are copied');
    ok(!isset($s['diagnostics']), 'no diagnostics on success');
});

test('notes: a guard refusal is not done, keeps its cause, and says nothing was written', function () {
    $w  = notesWorld();
    $id = notesCall($w, 'notes_edit', ['note' => NOTES_NOTE, 'item_id' => NOTES_ITEM, 'replace' => 'Liber 4, Page 31.',
                                       'expected_revision' => NOTES_REVISION])['structuredContent']['task_id'];
    list(, $lease) = claimTask($w, $id);
    completeTask($w, $id, $lease, ['notes' => 'review_edit', 'ok' => false, 'cause' => 'guard', 'adw_id' => 'beefcafe'],
                 false);
    $s = notesCall($w, 'notes_get_result', ['task_id' => $id])['structuredContent'];
    eq(false, $s['verified']);
    eq('guard', $s['cause']);
    ok(strpos($s['next'], 'nothing was written') !== false, 'says nothing was written');
    ok(is_array($s['diagnostics']) && count($s['diagnostics']) > 0, 'steps listed');
});

test('notes: get_result reads only the caller\'s own notes tasks', function () {
    $w     = notesWorld();
    $id    = notesCall($w, 'notes_inspect', ['note' => NOTES_NOTE])['structuredContent']['task_id'];
    eq(true, notesCall($w, 'notes_get_result', ['task_id' => $id], $w->client2)['isError'], 'other client');
    $other = $w->submit()->json['task_id'];
    eq(true, notesCall($w, 'notes_get_result', ['task_id' => $other])['isError'], 'not a notes task');
});

test('notes: a rollback carries the page check through, like every other write', function () {
    $w  = notesWorld();
    $id = notesCall($w, 'notes_rollback', ['note' => NOTES_NOTE, 'snapshot_id' => 's-0123456789',
                                           'expected_revision' => NOTES_REVISION, 'chain' => true, 'page' => true])['structuredContent']['task_id'];
    list($prompt) = claimTask($w, $id);
    eq(['notes' => 'review_rollback', 'id' => NOTES_NOTE, 'snapshot_id' => 's-0123456789', 'expected_revision' => NOTES_REVISION,
        'chain' => true, 'page' => true], json_decode($prompt, true));
});

test('notes: initialize names the tools and the rule about resolving', function () {
    $text = notesWorld()->mcp('initialize', ['protocolVersion' => '2025-06-18'])->json['result']['instructions'];
    ok(strpos($text, 'notes_inspect') !== false, 'named in the instructions');
    ok(strpos($text, 'never make either up') !== false, 'and the rule about evidence or approval');
    $plain = (new World())->mcp('initialize', ['protocolVersion' => '2025-06-18'])->json['result']['instructions'];
    ok(strpos($plain, 'notes_') === false, 'not mentioned without the tools');
});
