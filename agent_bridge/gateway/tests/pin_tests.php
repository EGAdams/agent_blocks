<?php
declare(strict_types=1);
namespace AgentBridge\Tests;

use AgentBridge\TokenAuthenticator;

/** A gateway where workspace `cashy-r46` is pinned to one machine's worker token (`$w->pinned`). */
function pinWorld(): array
{
    $w = new World([
        'allowed_operations' => ['inspect', 'explain', 'search_memory', 'implement'],
        'allowed_modes'      => ['read_only', 'workspace_write'],
        'agents' => [
            ['name' => 'codex', 'display_name' => 'Codex', 'description' => 'reads code',
             'operations' => ['inspect', 'explain', 'search_memory'], 'workspaces' => ['agent_blocks']],
            ['name' => 'cashy-r46', 'display_name' => 'Cashy on R46', 'description' => 'banking navigation on R46',
             'operations' => ['inspect', 'implement'], 'workspaces' => ['cashy-r46'], 'write_workspaces' => ['cashy-r46']],
        ],
    ]);
    $issued = TokenAuthenticator::issue($w->pdo, $w->ids, $w->clock, 'worker', 'wsl-rosemary46');
    $w->rebuild(['worker_pins' => [$issued['id'] => ['cashy-r46']]]);
    return [$w, $issued['token']];
}

function cashyTask(World $w, string $op = 'status'): string
{
    return $w->submit(['agent' => 'cashy-r46', 'workspace' => 'cashy-r46', 'operation' => 'inspect',
                       'prompt' => json_encode(['cashy' => $op, 'machine' => 'R46', 'session' => 'bridge-r46'])])->json['task_id'];
}

test('pins: a pinned workspace is offered only to its own worker, in order, and the home worker never sees it', function () {
    [$w, $r46] = pinWorld();
    $home  = $w->submit()->json['task_id'];            // explain@agent_blocks, oldest
    $w->clock->t++;
    $cashy = cashyTask($w);
    eq($home,  $w->call('GET', '/worker/tasks/next', $w->worker)->json['task_id'], 'home worker: its own task');
    eq($cashy, $w->call('GET', '/worker/tasks/next', $r46)->json['task_id'], 'R46 worker: only the pinned task, though older work is queued');
    eq($cashy, $w->call('GET', '/worker/tasks/next', $r46, null, ['pinned' => '1'])->json['task_id'], 'pinned=1 is the same lane');
    $w->call('POST', "/worker/tasks/$home/claim", $w->worker, ['worker_id' => 'home']);
    eq(204, $w->call('GET', '/worker/tasks/next', $w->worker)->status, 'the home worker is never offered the pinned task');
});

test('pins: claiming across a pin is a 409 that changes nothing, either way round', function () {
    [$w, $r46] = pinWorld();
    $home  = $w->submit()->json['task_id'];
    $cashy = cashyTask($w);
    $wrong = $w->call('POST', "/worker/tasks/$cashy/claim", $w->worker, ['worker_id' => 'home']);
    eq(409, $wrong->status);
    eq('pinned_elsewhere', $wrong->json['error']['code'] ?? $wrong->json['code'] ?? '');
    eq(409, $w->call('POST', "/worker/tasks/$home/claim", $r46, ['worker_id' => 'r46'])->status, 'R46 cannot take home work');
    eq('queued', $w->call('GET', "/tasks/$cashy", $w->client)->json['state'], 'the refused claim left it queued');
    $ok = $w->call('POST', "/worker/tasks/$cashy/claim", $r46, ['worker_id' => 'wsl-rosemary46-machine']);
    eq(200, $ok->status);
    eq('cashy-r46', $ok->json['agent']);
});

test('pins: pinned=1 from an unpinned token is a 403, and a bad value is a 400 (the worker\'s probe)', function () {
    [$w, $r46] = pinWorld();
    eq(403, $w->call('GET', '/worker/tasks/next', $w->worker, null, ['pinned' => '1'])->status);
    eq(400, $w->call('GET', '/worker/tasks/next', $r46, null, ['pinned' => 'probe'])->status);
    eq(204, $w->call('GET', '/worker/tasks/next', $r46, null, ['pinned' => '1'])->status, 'nothing pinned queued');
});

test('pins: with no worker_pins configured, nothing changes', function () {
    $w  = new World();
    $id = $w->submit()->json['task_id'];
    eq($id, $w->call('GET', '/worker/tasks/next', $w->worker)->json['task_id']);
    eq(200, $w->call('POST', "/worker/tasks/$id/claim", $w->worker, ['worker_id' => 'w1'])->status);
});
