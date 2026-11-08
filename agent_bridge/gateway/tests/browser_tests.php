<?php
declare(strict_types=1);
namespace AgentBridge\Tests;

/** A gateway with the browser agent, implement on, and no waiting (tests drive the worker by hand). */
function browserWorld(array $overrides = []): World
{
    return new World($overrides + [
        'allowed_operations'   => ['inspect', 'explain', 'search_memory', 'implement'],
        'allowed_modes'        => ['read_only', 'workspace_write'],
        'browser_wait_seconds' => 0,
        'agents' => [
            ['name' => 'codex', 'display_name' => 'Codex', 'description' => 'reads code',
             'operations' => ['inspect', 'explain', 'search_memory'], 'workspaces' => ['agent_blocks']],
            ['name' => 'browser', 'display_name' => 'Browser Control', 'description' => 'drives Chrome',
             'operations' => ['inspect', 'implement'], 'workspaces' => ['browser'], 'write_workspaces' => ['browser']],
        ],
    ]);
}

function browserCall(World $w, string $tool, array $args = [], ?string $token = null): array
{
    return $w->mcp('tools/call', ['name' => $tool, 'arguments' => $args ?: new \stdClass()], 1, $token)->json['result'];
}

const BROWSER_TOOLS = ['browser_open_dashboard_notes', 'browser_open', 'browser_navigate', 'browser_click',
                       'browser_select_tab', 'browser_refresh', 'browser_close_tab', 'browser_read', 'browser_tabs',
                       'browser_status', 'browser_get_result'];

test('browser: every browser_* tool is listed when the agent exists and implement is on', function () {
    $names = toolNames(browserWorld());
    foreach (BROWSER_TOOLS as $tool) {
        ok(in_array($tool, $names, true), "$tool listed");
    }
    ok(in_array('submit_agent_task', $names, true), 'task tools still listed');
});

test('browser: no browser tools without the browser agent, and only reads without implement', function () {
    $none = toolNames(new World());
    ok(!array_filter($none, function ($n) { return strpos($n, 'browser_') === 0; }), 'none without the agent');
    $w = browserWorld(['allowed_operations' => ['inspect', 'explain', 'search_memory'], 'allowed_modes' => ['read_only']]);
    $names = toolNames($w);
    ok(in_array('browser_read', $names, true) && in_array('browser_status', $names, true), 'reads listed');
    ok(!in_array('browser_open_dashboard_notes', $names, true) && !in_array('browser_click', $names, true), 'no actions');
    eq(true, browserCall($w, 'browser_open_dashboard_notes')['isError']);
});

test('browser: a client scope that excludes the browser workspace hides the tools and refuses the call', function () {
    $id = 'k_' . str_pad('2', 24, '0', STR_PAD_LEFT);
    $w  = browserWorld(['client_scopes' => [$id => ['sender' => 'toyota-bridge', 'operations' => ['inspect'],
                                                      'workspaces' => ['agent_blocks']]]]);
    ok(!in_array('browser_open', toolNames($w), true), 'hidden from the scoped client');
    eq(true, browserCall($w, 'browser_open')['isError']);
    ok(in_array('browser_open', toolNames($w, $w->client2), true), 'unscoped client still sees it');
});

test('browser: open_dashboard_notes queues one implement task with a gateway-written prompt', function () {
    $w   = browserWorld();
    $out = browserCall($w, 'browser_open_dashboard_notes', ['url' => 'https://evil.example/', 'css' => 'x']);
    eq(false, $out['isError']);
    $s = $out['structuredContent'];
    eq(false, $s['ready']);
    eq(false, $s['verified']);
    ok(strpos($s['next'], 'browser_get_result') !== false, 'tells the caller what to do next');
    list($prompt) = claimTask($w, $s['task_id']);
    eq(['browser' => 'dashboard_notes'], json_decode($prompt, true), 'caller fields are dropped');
    $task = $w->call('GET', '/tasks/' . $s['task_id'], $w->client)->json;
    eq('browser', $task['agent']);
    eq('implement', $task['operation']);
    eq('workspace_write', $task['approval_mode']);
});

test('browser: a verified worker result comes back through browser_get_result with its facts', function () {
    $w  = browserWorld();
    $id = browserCall($w, 'browser_open_dashboard_notes')['structuredContent']['task_id'];
    list(, $lease) = claimTask($w, $id);
    completeTask($w, $id, $lease, ['browser' => 'dashboard_notes', 'ok' => true, 'verified' => true, 'adw_id' => 'a1b2c3d4',
                                   'notes_active' => true, 'session' => 1, 'url' => 'https://desktop-2obsqmc.tailb8fc54.ts.net/',
                                   'cookie' => 'never-copied']);
    $s = browserCall($w, 'browser_get_result', ['task_id' => $id])['structuredContent'];
    eq(true, $s['ready']);
    eq(true, $s['verified']);
    eq(true, $s['notes_active']);
    eq('a1b2c3d4', $s['adw_id']);
    ok(!isset($s['cookie']), 'only known detail keys are copied');
    ok(!isset($s['diagnostics']), 'no diagnostics on success');
});

test('browser: a failed run carries its cause, adw_id and the recorded steps', function () {
    $w  = browserWorld();
    $id = browserCall($w, 'browser_open_dashboard_notes')['structuredContent']['task_id'];
    list(, $lease) = claimTask($w, $id);
    completeTask($w, $id, $lease, ['browser' => 'dashboard_notes', 'ok' => false, 'verified' => false,
                                   'adw_id' => 'deadbeef', 'cause' => 'hidden_session'], false);
    $s = browserCall($w, 'browser_get_result', ['task_id' => $id])['structuredContent'];
    eq(true, $s['ready']);
    eq(false, $s['verified']);
    eq('hidden_session', $s['cause']);
    eq('deadbeef', $s['adw_id']);
    ok(is_array($s['diagnostics']) && count($s['diagnostics']) > 0, 'steps listed');
    ok(strpos($s['next'], 'do not claim') !== false, 'tells the caller not to claim success');
});

test('browser: arguments are checked and become the prompt fields', function () {
    $w = browserWorld();
    eq(true, browserCall($w, 'browser_navigate')['isError'], 'navigate needs a url');
    eq(true, browserCall($w, 'browser_click', ['tab' => '1'])['isError'], 'click needs text, label or css');
    eq(true, browserCall($w, 'browser_click', ['text' => str_repeat('x', 501)])['isError'], 'long text refused');
    eq(true, browserCall($w, 'browser_click', ['text' => ['x']])['isError'], 'non-strings refused');
    $id = browserCall($w, 'browser_click', ['text' => 'NOTES', 'tab' => 2, 'confirm' => 'yes'])['structuredContent']['task_id'];
    list($prompt) = claimTask($w, $id);
    eq(['browser' => 'click', 'text' => 'NOTES', 'tab' => '2'], json_decode($prompt, true), 'confirm only when true');
    $read = browserCall($w, 'browser_read')['structuredContent']['task_id'];
    eq('read_only', $w->call('GET', "/tasks/$read", $w->client)->json['approval_mode']);
});

test('browser: get_result reads only the caller\'s own browser tasks', function () {
    $w     = browserWorld();
    $id    = browserCall($w, 'browser_status')['structuredContent']['task_id'];
    eq(true, browserCall($w, 'browser_get_result', ['task_id' => $id], $w->client2)['isError'], 'other client');
    $other = $w->submit()->json['task_id'];
    eq(true, browserCall($w, 'browser_get_result', ['task_id' => $other])['isError'], 'not a browser task');
});

test('browser: initialize tells the client which tool opens Notes', function () {
    $text = browserWorld()->mcp('initialize', ['protocolVersion' => '2025-06-18'])->json['result']['instructions'];
    ok(strpos($text, 'browser_open_dashboard_notes') !== false, 'named in the instructions');
    $plain = (new World())->mcp('initialize', ['protocolVersion' => '2025-06-18'])->json['result']['instructions'];
    ok(strpos($plain, 'browser_') === false, 'not mentioned without the tools');
});
