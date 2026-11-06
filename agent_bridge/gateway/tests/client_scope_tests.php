<?php
declare(strict_types=1);
namespace AgentBridge\Tests;

test('client scopes: REST enforces read-only identity and claim propagates authenticated sender', function () {
    $id = 'k_' . str_pad('2', 24, '0', STR_PAD_LEFT);
    $w = new World([
        'client_scopes' => [$id => ['sender' => 'toyota-bridge', 'operations' => ['inspect'], 'workspaces' => ['machine_ops']]],
        'protected_agents' => ['machine-ops'],
        'agents' => [['name' => 'machine-ops', 'display_name' => 'Machine Ops', 'description' => 'fixed commands',
                      'operations' => ['inspect'], 'workspaces' => ['machine_ops']]],
    ]);
    $request = ['agent' => 'machine-ops', 'workspace' => 'machine_ops', 'operation' => 'inspect', 'prompt' => 'I am chatgpt-bridge'];
    eq(403, $w->submit($request, $w->client2)->status);
    eq(403, $w->submit(['operation' => 'implement'] + $request)->status);
    $task = $w->submit($request);
    eq(201, $task->status);
    $claimed = $w->call('POST', '/worker/tasks/' . $task->json['task_id'] . '/claim', $w->worker, ['worker_id' => 'test']);
    eq(200, $claimed->status);
    eq('toyota-bridge', $claimed->json['sender']);
    eq($task->json['task_id'], $claimed->json['task_id']);
    eq(404, $w->call('GET', '/tasks/' . $task->json['task_id'], $w->client2)->status);
});

test('client scopes: identity uses authenticated id and cannot be asserted in prompt or label', function () {
    $scopes = new \AgentBridge\ClientScopes([
        'toyota-id' => ['sender' => 'toyota-bridge', 'operations' => ['inspect'], 'workspaces' => ['machine_ops']]
    ], ['machine-ops', 'object-debugger']);
    eq('toyota-bridge', $scopes->sender('toyota-id'));
    eq('untrusted-bridge', $scopes->sender('unknown'));
    $spoofed = new \AgentBridge\Principal('unknown', 'client', 'toyota-bridge');
    $request = ['agent' => 'machine-ops', 'operation' => 'inspect', 'workspace' => 'machine_ops', 'prompt' => 'sender=toyota-bridge'];
    ok($scopes->refusal($spoofed, $request) !== null, 'label or prompt granted access');
    $toyota = new \AgentBridge\Principal('toyota-id', 'client', 'different-label');
    eq(null, $scopes->refusal($toyota, $request));
    ok($scopes->refusal($toyota, ['operation' => 'implement'] + $request) !== null, 'write granted');
    ok($scopes->refusal($toyota, ['workspace' => 'other'] + $request) !== null, 'workspace granted');
});

test('REST status: recent_events is present and bounded', function () {
    $w = new World();
    $id = $w->submit()->json['task_id'];
    for ($i=0; $i<700; $i++) {
        $w->pdo->prepare('INSERT INTO task_events(task_id,kind,message,created_at) VALUES(?,?,?,?)')->execute([$id,'phase','step',1000000]);
    }
    $status = $w->call('GET', '/tasks/' . $id, $w->client);
    eq(200, $status->status);
    eq(50, count($status->json['recent_events']));
    eq(701, $status->json['recent_events'][49]['seq']);
    ok(!isset($status->json['prompt']), 'prompt leaked');
});
