<?php
declare(strict_types=1);
namespace AgentBridge\Tests;

/** A gateway with BATI's desktop agent (inspect and implement) and no waiting. */
function batiWorld(array $overrides = []): World
{
    return new World($overrides + [
        'allowed_operations' => ['inspect', 'explain', 'search_memory', 'implement'],
        'allowed_modes'      => ['read_only', 'workspace_write'],
        'bati_wait_seconds'  => 0,
        'agents' => [
            ['name' => 'codex', 'display_name' => 'Codex', 'description' => 'reads code',
             'operations' => ['inspect', 'explain', 'search_memory'], 'workspaces' => ['agent_blocks']],
            ['name' => 'bati-desktop', 'display_name' => 'BATI desktop Chrome', 'description' => 'Chrome on BATI',
             'operations' => ['inspect', 'implement'], 'workspaces' => ['bati-desktop'], 'write_workspaces' => ['bati-desktop']],
        ],
    ]);
}

function batiCall(World $w, string $tool, array $args = [], ?string $token = null): array
{
    return $w->mcp('tools/call', ['name' => $tool, 'arguments' => $args ?: new \stdClass()], 1, $token)->json['result'];
}

const BATI_READS = ['bati_status', 'bati_read', 'bati_screenshot', 'bati_get_result'];
const BATI_STEPS = ['bati_open', 'bati_show_view', 'bati_plan_for_today'];

function batiTask(World $w, string $id): array
{
    return $w->pdo->query("SELECT agent, workspace, operation, approval_mode FROM tasks WHERE id = " . $w->pdo->quote($id))
                   ->fetch(\PDO::FETCH_ASSOC);
}

test('bati: seven tools listed; reads are read-only, steps are not destructive', function () {
    $w     = batiWorld();
    $names = toolNames($w);
    foreach (array_merge(BATI_READS, BATI_STEPS) as $tool) {
        ok(in_array($tool, $names, true), "$tool listed");
    }
    foreach ($w->mcp('tools/list', [], 1)->json['result']['tools'] as $t) {
        if (in_array($t['name'], BATI_READS, true)) {
            eq(true, $t['annotations']['readOnlyHint'], $t['name'] . ' read-only');
        } elseif (in_array($t['name'], BATI_STEPS, true)) {
            eq(false, $t['annotations']['readOnlyHint'], $t['name'] . ' writes');
            eq(false, $t['annotations']['destructiveHint'], $t['name'] . ' not destructive');
        }
    }
});

test('bati: no agent in the registry means no tools', function () {
    $names = toolNames(browserWorld());
    eq([], array_values(array_filter($names, function ($n) { return strpos($n, 'bati_') === 0; })));
});

test('bati: every prompt names BATI, and reads are inspect while steps are implement', function () {
    $w = batiWorld();
    foreach ([['bati_status', [], ['bati' => 'status', 'machine' => 'BATI'], 'inspect'],
              ['bati_read', ['view' => 'Plan for Today'], ['bati' => 'read', 'machine' => 'BATI', 'view' => 'plan-for-today'], 'inspect'],
              ['bati_screenshot', [], ['bati' => 'screenshot', 'machine' => 'BATI'], 'inspect'],
              ['bati_open', ['url' => 'http://localhost:8765/'], ['bati' => 'open', 'machine' => 'BATI', 'url' => 'http://localhost:8765/'], 'implement'],
              ['bati_show_view', [], ['bati' => 'show_view', 'machine' => 'BATI', 'view' => 'plan-for-today'], 'implement'],
              ['bati_plan_for_today', [], ['bati' => 'plan_for_today', 'machine' => 'BATI'], 'implement']] as $case) {
        $r = batiCall($w, $case[0], $case[1]);
        eq(false, $r['isError'], $case[0]);
        $id = $r['structuredContent']['task_id'];
        $t  = batiTask($w, $id);
        eq(['bati-desktop', 'bati-desktop', $case[3]], [$t['agent'], $t['workspace'], $t['operation']], $case[0]);
        list($prompt) = claimTask($w, $id);
        eq($case[2], json_decode($prompt, true), $case[0] . ' prompt');
    }
});

test('bati: the caller cannot name another machine, and bad urls or views queue nothing', function () {
    $w = batiWorld();
    $r = batiCall($w, 'bati_status', ['machine' => 'SQMC']);
    list($prompt) = claimTask($w, $r['structuredContent']['task_id']);
    eq(['bati' => 'status', 'machine' => 'BATI'], json_decode($prompt, true), 'a caller\'s machine is ignored');
    $before = (int) $w->pdo->query('SELECT COUNT(*) FROM tasks')->fetchColumn();
    foreach ([['bati_open', ['url' => 'https://example.com/']],
              ['bati_open', ['url' => 'http://user:pw@localhost:8765/']], ['bati_open', ['url' => 'http://localhost:9222/']],
              ['bati_open', ['url' => '--help']], ['bati_open', ['url' => 'http://localhost:8765/' . str_repeat('a', 300)]],
              ['bati_show_view', ['view' => 'settings']], ['bati_read', ['view' => 'finance']],
              ['bati_plan_for_today', ['url' => 'file:///etc/passwd']]] as $case) {
        $r = batiCall($w, $case[0], $case[1]);
        eq(true, $r['isError'], $case[0] . ' ' . json_encode($case[1]));
    }
    eq($before, (int) $w->pdo->query('SELECT COUNT(*) FROM tasks')->fetchColumn());
});

test('bati: a verified result says so; an unverified one says what to do next and nothing else', function () {
    $w  = batiWorld();
    $id = batiCall($w, 'bati_plan_for_today')['structuredContent']['task_id'];
    list(, $lease) = claimTask($w, $id);
    completeTask($w, $id, $lease, ['bati' => 'plan_for_today', 'ok' => true, 'verified' => true, 'cause' => '',
                                   'adw_id' => 'abc12345', 'machine' => ['hostname' => 'desktop-shdbati', 'alias' => 'BATI'],
                                   'data' => ['page' => ['heading' => 'Plan for Today — November 21, 2026']]]);
    $got = batiCall($w, 'bati_get_result', ['task_id' => $id])['structuredContent'];
    eq(true, $got['verified']);
    eq('abc12345', $got['adw_id']);
    eq('desktop-shdbati', $got['machine']['hostname']);

    $id = batiCall($w, 'bati_status')['structuredContent']['task_id'];
    list(, $lease) = claimTask($w, $id);
    completeTask($w, $id, $lease, ['bati' => 'status', 'ok' => false, 'verified' => false, 'cause' => 'no_desktop'], false);
    $got = batiCall($w, 'bati_get_result', ['task_id' => $id])['structuredContent'];
    eq(false, $got['verified']);
    ok(strpos($got['next'], 'sign in on BATI') !== false, 'says to sign in');
});

test('bati: another client cannot read the result', function () {
    $w  = batiWorld();
    $id = batiCall($w, 'bati_status')['structuredContent']['task_id'];
    eq(true, batiCall($w, 'bati_get_result', ['task_id' => $id], $w->client2)['isError']);
});
