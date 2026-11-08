<?php
declare(strict_types=1);
namespace AgentBridge\Tests;

/** A gateway with the ChatGPT relay agent, implement on, and no waiting (tests drive the worker by hand). */
function relayWorld(array $overrides = []): World
{
    return new World($overrides + [
        'allowed_operations' => ['inspect', 'explain', 'search_memory', 'implement'],
        'allowed_modes'      => ['read_only', 'workspace_write'],
        'relay_wait_seconds' => 0,
        'agents' => [
            ['name' => 'codex', 'display_name' => 'Codex', 'description' => 'reads code',
             'operations' => ['inspect', 'explain', 'search_memory'], 'workspaces' => ['agent_blocks']],
            ['name' => 'chatgpt-relay', 'display_name' => 'ChatGPT Relay', 'description' => 'speaks to a ChatGPT session',
             'operations' => ['inspect', 'implement'], 'workspaces' => ['chatgpt-relay'],
             'write_workspaces' => ['chatgpt-relay']],
        ],
    ]);
}

function relayCall(World $w, string $tool, array $args = [], ?string $token = null): array
{
    return $w->mcp('tools/call', ['name' => $tool, 'arguments' => $args ?: new \stdClass()], 1, $token)->json['result'];
}

const RELAY_TOOLS = ['chatgpt_ask', 'chatgpt_type', 'chatgpt_send', 'chatgpt_open', 'chatgpt_new_chat',
                     'chatgpt_read', 'chatgpt_sessions', 'chatgpt_status', 'chatgpt_get_result'];

test('relay: every chatgpt_* tool is listed when the agent exists and implement is on', function () {
    $names = toolNames(relayWorld());
    foreach (RELAY_TOOLS as $tool) {
        ok(in_array($tool, $names, true), "$tool listed");
    }
    ok(in_array('submit_agent_task', $names, true), 'task tools still listed');
});

test('relay: nothing can close that browser, because it is signed in', function () {
    $names = toolNames(relayWorld());
    foreach (['chatgpt_quit', 'chatgpt_close', 'chatgpt_shutdown', 'chatgpt_logout'] as $tool) {
        ok(!in_array($tool, $names, true), "$tool is not offered");
        eq(true, relayCall(relayWorld(), $tool)['isError'], "$tool is refused");
    }
});

test('relay: no chatgpt tools without the relay agent, and only reads without implement', function () {
    $none = toolNames(new World());
    ok(!array_filter($none, function ($n) { return strpos($n, 'chatgpt_') === 0; }), 'none without the agent');
    $w = relayWorld(['allowed_operations' => ['inspect', 'explain', 'search_memory'], 'allowed_modes' => ['read_only']]);
    $names = toolNames($w);
    ok(in_array('chatgpt_read', $names, true) && in_array('chatgpt_status', $names, true), 'reads listed');
    ok(in_array('chatgpt_sessions', $names, true), 'listing the sessions is a read');
    ok(!in_array('chatgpt_ask', $names, true) && !in_array('chatgpt_send', $names, true), 'no sending');
    ok(!in_array('chatgpt_new_chat', $names, true), 'no new chat');
    eq(true, relayCall($w, 'chatgpt_ask', ['message' => 'hello'])['isError']);
});

test('relay: a client scope that excludes the relay workspace hides the tools and refuses the call', function () {
    $id = 'k_' . str_pad('2', 24, '0', STR_PAD_LEFT);
    $w  = relayWorld(['client_scopes' => [$id => ['sender' => 'toyota-bridge', 'operations' => ['inspect'],
                                                    'workspaces' => ['agent_blocks']]]]);
    ok(!in_array('chatgpt_ask', toolNames($w), true), 'hidden from the scoped client');
    eq(true, relayCall($w, 'chatgpt_ask', ['message' => 'hi'])['isError']);
    ok(in_array('chatgpt_ask', toolNames($w, $w->client2), true), 'unscoped client still sees it');
});

test('relay: ask queues one implement task with a gateway-written prompt', function () {
    $w   = relayWorld();
    $out = relayCall($w, 'chatgpt_ask', ['message' => 'what county is Fralick\'s Addition in?', 'instance' => 'bati',
                                         'css' => '#not-a-relay-field']);
    eq(false, $out['isError']);
    $s = $out['structuredContent'];
    eq(false, $s['ready']);
    eq(false, $s['verified']);
    ok(strpos($s['next'], 'chatgpt_get_result') !== false, 'tells the caller what to do next');
    ok(strpos($s['next'], 'do not resend') !== false, 'and not to resend a message that may already be sent');
    list($prompt) = claimTask($w, $s['task_id']);
    eq(['relay' => 'ask', 'instance' => 'bati', 'message' => 'what county is Fralick\'s Addition in?'],
       json_decode($prompt, true), 'unknown caller fields are dropped');
    $task = $w->call('GET', '/tasks/' . $s['task_id'], $w->client)->json;
    eq('chatgpt-relay', $task['agent']);
    eq('implement', $task['operation']);
    eq('workspace_write', $task['approval_mode']);
});

test('relay: a finished reply comes back verified, with the reply and the adw_id', function () {
    $w  = relayWorld();
    $id = relayCall($w, 'chatgpt_ask', ['message' => 'relay check'])['structuredContent']['task_id'];
    list(, $lease) = claimTask($w, $id);
    completeTask($w, $id, $lease, ['relay' => 'ask', 'ok' => true, 'verified' => true, 'adw_id' => '5ea1a596',
                                   'instance' => 'bati', 'reply' => 'RELAY OK', 'settled' => true, 'waited' => 14.18,
                                   'session_cookie' => 'never-copied']);
    $s = relayCall($w, 'chatgpt_get_result', ['task_id' => $id])['structuredContent'];
    eq(true, $s['ready']);
    eq(true, $s['verified']);
    eq('RELAY OK', $s['reply']);
    eq('5ea1a596', $s['adw_id']);
    ok(!isset($s['session_cookie']), 'only known detail keys are copied');
    ok(!isset($s['diagnostics']), 'no diagnostics on success');
});

test('relay: a reply that was still being written is not the answer, and says so', function () {
    $w  = relayWorld();
    $id = relayCall($w, 'chatgpt_ask', ['message' => 'write me a long one'])['structuredContent']['task_id'];
    list(, $lease) = claimTask($w, $id);
    completeTask($w, $id, $lease, ['relay' => 'ask', 'ok' => true, 'verified' => false, 'adw_id' => 'beef0001',
                                   'settled' => false, 'reply' => 'The plat was fil'], false);
    $s = relayCall($w, 'chatgpt_get_result', ['task_id' => $id])['structuredContent'];
    eq(true, $s['ready']);
    eq(false, $s['verified']);
    ok(strpos($s['next'], 'chatgpt_read') !== false, 'points at the finished reply');
    ok(strpos($s['next'], 'do not treat the text above as the answer') !== false, 'and says not to use half a sentence');
    ok(is_array($s['diagnostics']) && count($s['diagnostics']) > 0, 'steps listed');
});

test('relay: a refused credential is a failure that says why, and never echoes what was typed', function () {
    $w  = relayWorld();
    $id = relayCall($w, 'chatgpt_ask', ['message' => 'the password is hunter2'])['structuredContent']['task_id'];
    list(, $lease) = claimTask($w, $id);
    completeTask($w, $id, $lease, ['relay' => 'ask', 'ok' => false, 'verified' => false, 'adw_id' => 'deadbee5',
                                   'cause' => 'credential'], false);
    $s = relayCall($w, 'chatgpt_get_result', ['task_id' => $id])['structuredContent'];
    eq(false, $s['verified']);
    eq('credential', $s['cause']);
    ok(strpos(json_encode($s), 'hunter2') === false, 'the message never comes back out');
    ok(strpos($s['next'], 'do not claim') !== false, 'tells the caller not to claim it was delivered');
});

test('relay: arguments are checked and become the prompt fields', function () {
    $w = relayWorld();
    eq(true, relayCall($w, 'chatgpt_ask')['isError'], 'ask needs a message');
    eq(true, relayCall($w, 'chatgpt_type')['isError'], 'type needs a message');
    eq(true, relayCall($w, 'chatgpt_ask', ['message' => ['hi']])['isError'], 'non-strings refused');
    eq(true, relayCall($w, 'chatgpt_ask', ['message' => str_repeat('x', 7001)])['isError'], 'a book is refused');
    eq(true, relayCall($w, 'chatgpt_ask', ['message' => 'hi', 'instance' => str_repeat('x', 501)])['isError'], 'long values refused');
    $id = relayCall($w, 'chatgpt_ask', ['message' => 'over to you', 'origin' => 'bati', 'hops' => 1,
                                        'no_wait' => true])['structuredContent']['task_id'];
    list($prompt) = claimTask($w, $id);
    eq(['relay' => 'ask', 'message' => 'over to you', 'hops' => '1', 'origin' => 'bati', 'no_wait' => true],
       json_decode($prompt, true), 'numbers arrive as text, no_wait as a switch');
    $read = relayCall($w, 'chatgpt_read', ['last' => 6])['structuredContent']['task_id'];
    eq('read_only', $w->call('GET', "/tasks/$read", $w->client)->json['approval_mode']);
    list($asked) = claimTask($w, $read);
    eq(['relay' => 'read', 'last' => '6'], json_decode($asked, true), 'last arrives as text');
});

test('relay: a new chat needs an explicit yes, because it abandons the thread on screen', function () {
    $w = relayWorld();
    eq(true, relayCall($w, 'chatgpt_new_chat')['isError'], 'refused with no confirm');
    eq(true, relayCall($w, 'chatgpt_new_chat', ['confirm' => 'yes'])['isError'], 'the word yes is not a yes');
    eq(true, relayCall($w, 'chatgpt_new_chat', ['confirm' => false])['isError'], 'false is not a yes');
    $id = relayCall($w, 'chatgpt_new_chat', ['confirm' => true])['structuredContent']['task_id'];
    list($prompt) = claimTask($w, $id);
    eq(['relay' => 'new_chat', 'confirm' => true], json_decode($prompt, true), 'confirm only when exactly true');
});

test('relay: a session-to-session message starts by listing the sessions', function () {
    $w  = relayWorld();
    $id = relayCall($w, 'chatgpt_sessions')['structuredContent']['task_id'];
    eq('read_only', $w->call('GET', "/tasks/$id", $w->client)->json['approval_mode']);
    list($prompt, $lease) = claimTask($w, $id);
    eq(['relay' => 'instances'], json_decode($prompt, true));
    completeTask($w, $id, $lease, ['relay' => 'instances', 'ok' => true, 'verified' => true, 'adw_id' => 'aaaa1111',
                                   'instances' => [['label' => 'bati', 'base_url' => 'http://100.80.49.10:5001']]]);
    $s = relayCall($w, 'chatgpt_get_result', ['task_id' => $id])['structuredContent'];
    eq(true, $s['verified']);
    eq('bati', $s['instances'][0]['label']);
});

test('relay: get_result reads only the caller\'s own relay tasks', function () {
    $w     = relayWorld();
    $id    = relayCall($w, 'chatgpt_status')['structuredContent']['task_id'];
    eq(true, relayCall($w, 'chatgpt_get_result', ['task_id' => $id], $w->client2)['isError'], 'other client');
    $other = $w->submit()->json['task_id'];
    eq(true, relayCall($w, 'chatgpt_get_result', ['task_id' => $other])['isError'], 'not a relay task');
});

test('relay: initialize tells the client the relay exists and what not to do with it', function () {
    $text = relayWorld()->mcp('initialize', ['protocolVersion' => '2025-06-18'])->json['result']['instructions'];
    ok(strpos($text, 'chatgpt_ask') !== false, 'named in the instructions');
    ok(strpos($text, 'chatgpt_sessions') !== false, 'and where a session-to-session message starts');
    ok(strpos($text, 'never resend') !== false, 'and the one mistake that posts twice');
    $plain = (new World())->mcp('initialize', ['protocolVersion' => '2025-06-18'])->json['result']['instructions'];
    ok(strpos($plain, 'chatgpt_') === false, 'not mentioned without the tools');
});
