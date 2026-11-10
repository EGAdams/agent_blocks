<?php
declare(strict_types=1);

// The gateway's queue watch (AlertWatch.php). Required by run_tests.php; uses its
// World, test(), eq() and ok(), and printer_tests.php's helpers. Each simulation
// from the 2026-11-10 watchdog brief is one test: a stuck print, an ADW that runs
// too long, a crashed worker, a Bridge disconnect, a watch restart, and a stalled
// job that recovers. No printer is touched and nothing is reprinted: the worker
// side is played by claim/complete calls, and every test counts print tasks.

namespace AgentBridge\Tests;

use AgentBridge\AlertEscalation;
use AgentBridge\Bootstrap;
use AgentBridge\QueueWatchPolicy;

function alertWorld(array $watch = []): World
{
    return printWorld(['alert_viewers' => ['test client'], 'watch' => $watch]);
}

/** A forced refresh through the REST route the box's supervisor reads, with the worker token. */
function alertsNow(World $w): array
{
    $r = $w->call('GET', '/alerts', $w->worker);
    eq(200, $r->status, 'GET /alerts');
    return $r->json;
}

function alertOf(array $snapshot, string $kind, ?string $taskId = null): ?array
{
    foreach ($snapshot['alerts'] as $a) {
        if ($a['kind'] === $kind && ($taskId === null || $a['task_id'] === $taskId)) {
            return $a;
        }
    }
    return null;
}

function resolvedOf(array $snapshot, string $kind, ?string $taskId = null): ?array
{
    foreach ($snapshot['recently_resolved'] as $a) {
        if ($a['kind'] === $kind && ($taskId === null || $a['task_id'] === $taskId)) {
            return $a;
        }
    }
    return null;
}

function poll(World $w, array $query = []): int
{
    return $w->call('GET', '/worker/tasks/next', $w->worker, null, $query)->status;
}

function auditCount(World $w, string $action): int
{
    $stmt = $w->pdo->prepare('SELECT COUNT(*) FROM audit_events WHERE action = ?');
    $stmt->execute([$action]);
    return (int) $stmt->fetchColumn();
}

function alertRows(World $w): int
{
    return (int) $w->pdo->query('SELECT COUNT(*) FROM alerts')->fetchColumn();
}

function queuePrint(World $w): string
{
    $r = tool($w, 'printer_print_text', ['text' => 'Deed Notary Plan', 'title' => 'Deed Notary Plan', 'confirm' => true]);
    eq(false, $r['isError'], 'print accepted');
    return $r['structuredContent']['job_id'];
}

test('watch: escalation levels are pure: notice, escalated at 15 min, urgent at an hour', function () {
    $e = new AlertEscalation([900, 3600]);
    eq(1, $e->level(1000, 1899));
    eq(2, $e->level(1000, 1900));
    eq(3, $e->level(1000, 4600));
    eq(3, $e->level(1000, 999999));
    eq('urgent', AlertEscalation::name(3));
    eq('93 min', QueueWatchPolicy::span(5580), 'the 93 minutes of 2026-11-10');
});

test('watch: alerts are offered only to EG\'s identities, and the REST read needs a viewer or the worker', function () {
    $w = alertWorld();
    ok(in_array('bridge_alerts', toolNames($w), true), 'viewer sees bridge_alerts');
    ok(!in_array('bridge_alerts', toolNames($w, $w->client2), true), 'other client must not');
    eq(true, tool($w, 'bridge_alerts', [], $w->client2)['isError']);
    eq(403, $w->call('GET', '/alerts', $w->client2)->status);
    eq(401, $w->call('GET', '/alerts', null)->status);
    eq(200, $w->call('GET', '/alerts', $w->client)->status);
    eq(403, $w->call('POST', '/alerts/al_000000000000000000000001/ack', $w->worker, [])->status, 'the worker cannot acknowledge');
});

test('simulation 1 — stuck print: queued past the threshold raises one alert with job, problem, duration and action', function () {
    $w   = alertWorld();
    $job = queuePrint($w);
    $w->clock->t += 299;
    eq(null, alertOf(alertsNow($w), 'task_queued_too_long'), 'not before queued_device_seconds');
    $w->clock->t += 2;
    $a = alertOf(alertsNow($w), 'task_queued_too_long', $job);
    ok($a !== null, 'stuck print alert');
    eq($job, $a['task_id']);
    eq('notice', $a['severity']);
    eq(301, $a['duration_seconds']);
    ok(strpos($a['problem'], 'Print job "Deed Notary Plan" ' . $job) === 0, $a['problem']);
    ok(strpos($a['recommended_action'], 'Do not resend the print') === 0, $a['recommended_action']);
    eq('the condition clears', $a['stays_until']);
    alertsNow($w);
    alertsNow($w);
    eq(1, alertRows($w), 'no duplicate alert for the same stuck job');
    eq(1, printTasks($w), 'the watch never prints');
});

test('simulation 1 — the stuck print escalates once per level and every tool result carries it until acknowledged', function () {
    $w   = alertWorld();
    $job = queuePrint($w);
    $w->clock->t += 301;
    $id  = alertOf(alertsNow($w), 'task_queued_too_long', $job)['alert_id'];
    eq(1, auditCount($w, 'alert.raised'));

    $r     = tool($w, 'printer_list_recent');
    $block = end($r['content'])['text'];
    ok(strpos($block, 'BRIDGE ALERTS -- 1 unacknowledged') === 0, $block);
    ok(strpos($block, "job $job") !== false && strpos($block, 'Do not resend') !== false, $block);
    $shown = $w->pdo->query("SELECT shown_count, last_shown_to FROM alerts WHERE id = '$id'")->fetch(\PDO::FETCH_ASSOC);
    eq(1, (int) $shown['shown_count'], 'the reply that carried it is recorded');
    eq('test client', $shown['last_shown_to']);
    $other = $w->mcp('tools/call', ['name' => 'list_available_agents', 'arguments' => []], 1, $w->client2)->json['result'];
    eq(1, count($other['content']), 'another client never sees EG\'s alerts');

    $w->clock->t += 900;
    eq('escalated', alertOf(alertsNow($w), 'task_queued_too_long', $job)['severity']);
    alertsNow($w);
    eq(1, auditCount($w, 'alert.escalated'), 'escalated once, not every refresh');
    $w->clock->t += 2700;
    eq('urgent', alertOf(alertsNow($w), 'task_queued_too_long', $job)['severity']);
    eq(2, auditCount($w, 'alert.escalated'));
    ok(strpos(end(tool($w, 'printer_list_recent')['content'])['text'], '[URGENT]') !== false, 'urgent in the notice');

    $ack = tool($w, 'bridge_alert_acknowledge', ['alert_id' => $id, 'note' => 'EG will check the printer']);
    eq(false, $ack['isError']);
    eq(true, $ack['structuredContent']['acknowledged']);
    eq(null, $ack['structuredContent']['resolved_at'], 'a condition alert stays listed while the job is still stuck');
    eq(1, count(tool($w, 'printer_list_recent')['content']), 'acknowledged: no longer repeated in results');
    $w->clock->t += 7200;
    $a = alertOf(alertsNow($w), 'task_queued_too_long', $job);
    eq(true, $a['acknowledged']);
    eq('urgent', $a['severity']);
    eq(2, auditCount($w, 'alert.escalated'), 'an acknowledged alert does not escalate');
    eq(1, printTasks($w));
});

test('simulation 2 — ADW timeout: a task running past running_seconds raises an alert; finishing clears it', function () {
    $w = alertWorld(['running_seconds' => 600]);
    list($id, $lease) = $w->running();
    for ($i = 0; $i < 13; $i++) {
        $w->clock->t += 50;
        eq(200, $w->call('POST', "/worker/tasks/$id/heartbeat", $w->worker, ['lease_token' => $lease])->status);
    }
    $a = alertOf(alertsNow($w), 'task_running_too_long', $id);
    ok($a !== null, 'timeout alert');
    eq(null, alertOf(alertsNow($w), 'task_worker_lost', $id), 'its worker is still heartbeating');
    ok(strpos($a['recommended_action'], 'Do not resubmit') !== false, $a['recommended_action']);
    eq(200, $w->call('POST', "/worker/tasks/$id/complete", $w->worker,
                     ['lease_token' => $lease, 'ok' => true, 'summary' => 'done', 'body' => 'x'])->status);
    $snap = alertsNow($w);
    eq(null, alertOf($snap, 'task_running_too_long', $id));
    eq('the condition cleared', resolvedOf($snap, 'task_running_too_long', $id)['resolution']);
});

test('simulation 3 — crashed worker: lapsed lease, retry, then a sticky crash alert that waits for acknowledgement', function () {
    $w = alertWorld();
    list($id) = $w->running();
    $w->clock->t += 60 + 119;
    eq(null, alertOf(alertsNow($w), 'task_worker_lost', $id));
    $w->clock->t += 1;
    ok(alertOf(alertsNow($w), 'task_worker_lost', $id) !== null, 'a running task whose worker went quiet');

    eq(200, poll($w), 'the worker comes back; the gateway requeues the task');
    $snap = alertsNow($w);
    eq(null, alertOf($snap, 'task_worker_lost', $id), 'requeued, so no longer lost');
    eq(200, $w->call('POST', "/worker/tasks/$id/claim", $w->worker, ['worker_id' => 'w1'])->status);
    $retry = alertOf(alertsNow($w), 'task_retried', $id);
    ok($retry !== null && strpos($retry['problem'], 'attempt 2 of 2') !== false, json_encode($retry));

    $w->clock->t += 61;
    poll($w);
    $snap  = alertsNow($w);
    $crash = alertOf($snap, 'task_crashed', $id);
    ok($crash !== null, 'attempts exhausted by lease expiry');
    eq('acknowledged', $crash['stays_until']);
    eq(null, alertOf($snap, 'task_retried', $id), 'the retry alert clears once the task is final');

    $w->clock->t += 86400 * 3;
    ok(alertOf(alertsNow($w), 'task_crashed', $id) !== null, 'a failure is retained for days until acknowledged');
    $ack = $w->call('POST', '/alerts/' . $crash['alert_id'] . '/ack', $w->client, ['note' => 'seen']);
    eq(200, $ack->status);
    eq('acknowledged', $ack->json['resolution']);
    eq(null, alertOf(alertsNow($w), 'task_crashed', $id));
    alertsNow($w);
    eq(1, (int) $w->pdo->query("SELECT COUNT(*) FROM alerts WHERE kind = 'task_crashed'")->fetchColumn(),
       'an acknowledged failure is never raised again');
});

test('simulation 4 — Bridge disconnect: a worker that stops polling is reported, and its return clears it', function () {
    $w = alertWorld();
    eq(204, poll($w));
    eq(204, poll($w, ['skip' => 'implement@agent_blocks_dev']));
    eq(2, count(alertsNow($w)['workers']), 'two lanes, two contacts');
    $w->clock->t += 1199;
    eq(null, alertOf(alertsNow($w), 'worker_silent'));
    $job = queuePrint($w);
    $w->clock->t += 301;
    $snap   = alertsNow($w);
    $silent = array_values(array_filter($snap['alerts'], function ($a) { return $a['kind'] === 'worker_silent'; }));
    eq(2, count($silent), 'each lane separately');
    ok(strpos($silent[0]['problem'] . $silent[1]['problem'], 'takes every task') !== false, 'names the lane');
    $stuck = alertOf($snap, 'task_queued_too_long', $job);
    ok(strpos($stuck['problem'], 'looks offline') !== false, $stuck['problem']);
    eq(true, $stuck['details']['workers_silent']);

    eq(200, poll($w), 'the general lane reconnects and is offered the print');
    $snap = alertsNow($w);
    eq(1, count(array_filter($snap['alerts'], function ($a) { return $a['kind'] === 'worker_silent'; })),
       'only the lane still away stays reported');
    ok(resolvedOf($snap, 'worker_silent') !== null, 'the returned lane is resolved, and listed as resolved');
    eq(1, printTasks($w));
});

test('simulation 5 — watch restart: a fresh gateway over the same database keeps alerts and raises no duplicates', function () {
    $w   = alertWorld();
    $job = queuePrint($w);
    $w->clock->t += 301;
    $before = alertOf(alertsNow($w), 'task_queued_too_long', $job);
    $w->app = Bootstrap::build(['database' => ':memory:', 'alert_viewers' => ['test client'], 'max_body' => 65536,
                                'allowed_operations' => ['inspect', 'print', 'print_status'],
                                'allowed_modes' => ['read_only', 'device_control'], 'memory_agent' => 'codex',
                                'memory_workspace' => 'agent_blocks', 'lease_seconds' => 60, 'max_attempts' => 2,
                                'printer_scopes' => ['test client' => \AgentBridge\PrinterScopes::ALL]],
                               $w->pdo, $w->clock, new FakeIds());
    $w->clock->t += 10;
    $after = alertOf(alertsNow($w), 'task_queued_too_long', $job);
    eq($before['alert_id'], $after['alert_id'], 'the same alert, still open');
    eq($before['raised_at'], $after['raised_at']);
    eq(1, alertRows($w));
    $w->clock->t += 900;
    eq('escalated', alertOf(alertsNow($w), 'task_queued_too_long', $job)['severity'], 'escalation continues after restart');
});

test('simulation 5 — the watch runs on any request at most every refresh_seconds, and never breaks one', function () {
    $w   = alertWorld(['refresh_seconds' => 30]);
    $job = queuePrint($w);
    $w->clock->t += 301;
    eq(200, $w->call('GET', '/health', null)->status, 'an unauthenticated health check is enough to drive it');
    eq(1, alertRows($w), 'raised by the health request alone');
    $w->pdo->exec('DROP TABLE alerts');
    $w->clock->t += 31;
    eq(200, $w->call('GET', '/health', null)->status, 'a broken watch must not break the gateway');
});

test('simulation 6 — recovery: a stalled print picked up by a returning worker clears its alert, kept as history', function () {
    $w   = alertWorld();
    $job = queuePrint($w);
    $w->clock->t += 5580;
    $a = alertOf(alertsNow($w), 'task_queued_too_long', $job);
    eq('93 min', $a['duration']);
    claimTask($w, $job);
    $snap = alertsNow($w);
    eq(null, alertOf($snap, 'task_queued_too_long', $job));
    eq($a['alert_id'], resolvedOf($snap, 'task_queued_too_long', $job)['alert_id']);
    eq(1, printTasks($w), 'recovered without a second print');
});

test('the 2026-11-10 failure: a print that fails for no default printer raises a sticky alert and says never resend', function () {
    $w   = alertWorld();
    $job = queuePrint($w);
    list(, $lease) = claimTask($w, $job);
    eq(200, $w->call('POST', "/worker/tasks/$job/complete", $w->worker,
                     ['lease_token' => $lease, 'ok' => false, 'summary' => 'not printed. no default printer is set',
                      'body' => 'x', 'performer' => 'printer', 'details' => ['printer' => 'print', 'job' => ['state' => 'failed']]])->status);
    $a = alertOf(alertsNow($w), 'device_task_failed', $job);
    ok($a !== null, 'failed print alert');
    ok(strpos($a['problem'], 'no default printer is set') !== false, $a['problem']);
    ok(strpos($a['recommended_action'], 'Never resend a print without asking EG') !== false, $a['recommended_action']);
    eq(null, alertOf(alertsNow($w), 'print_unconfirmed', $job));
    eq(1, printTasks($w));
});

test('a print Windows accepted but never confirmed is flagged; a later spooler check clears it; ambiguous is sticky', function () {
    $w   = alertWorld();
    $job = queuePrint($w);
    list(, $lease) = claimTask($w, $job);
    completeTask($w, $job, $lease, ['printer' => 'print', 'job' => ['job_id' => $job, 'state' => 'submitted_to_spooler']]);
    $w->clock->t += 899;
    eq(null, alertOf(alertsNow($w), 'print_unconfirmed', $job));
    $w->clock->t += 1;
    $a = alertOf(alertsNow($w), 'print_unconfirmed', $job);
    ok($a !== null && strpos($a['problem'], 'Paper output is never verified') !== false, json_encode($a));
    $check = tool($w, 'printer_get_status', ['job_id' => $job, 'refresh' => true])['structuredContent']['check_task_id'];
    list(, $lease) = claimTask($w, $check);
    completeTask($w, $check, $lease, ['printer' => 'status', 'job' => ['state' => 'completed_per_spooler']]);
    eq(null, alertOf(alertsNow($w), 'print_unconfirmed', $job), 'the spooler finished it');

    $job2 = queuePrint($w);
    list(, $lease) = claimTask($w, $job2);
    completeTask($w, $job2, $lease, ['printer' => 'print', 'job' => ['job_id' => $job2, 'state' => 'ambiguous']]);
    $amb = alertOf(alertsNow($w), 'print_ambiguous', $job2);
    ok($amb !== null && strpos($amb['recommended_action'], 'Never reprint automatically') !== false, json_encode($amb));
    eq(2, printTasks($w), 'neither alert reprints');
});

test('bridge_alerts lists open and resolved alerts and worker contact, and is excluded from its own notice', function () {
    $w   = alertWorld();
    poll($w);
    $job = queuePrint($w);
    $w->clock->t += 301;
    $r = tool($w, 'bridge_alerts');
    eq(1, count($r['content']), 'no BRIDGE ALERTS block on bridge_alerts itself');
    $s = $r['structuredContent'];
    eq(1, $s['unacknowledged']);
    eq($job, $s['alerts'][0]['task_id']);
    eq(1, count($s['workers']));
    eq(true, tool($w, 'bridge_alert_acknowledge', ['alert_id' => 'nope'])['isError']);
    eq(true, tool($w, 'bridge_alert_acknowledge', ['alert_id' => 'al_ffffffffffffffffffffffff'])['isError']);
});
