<?php
declare(strict_types=1);

// Request supervision (RequestWatch.php). Required by run_tests.php after
// alert_tests.php; uses its helpers. The first test is the incident of
// 2026-11-10 as it happened: EG asked for two PDFs to be printed, neither was in
// the print outbox, the documents were not finished, ChatGPT made three
// printer_list checks and no print job was ever created. No printer is touched
// and nothing is printed: the worker side is played by claim/complete calls.

namespace AgentBridge\Tests;

use AgentBridge\Bootstrap;
use AgentBridge\RequestPolicy;

const SCAN_ONLY_OUTBOX = ['printer' => 'discover', 'default' => 'hp_8120e', 'recent' => [],
    'printers' => [['printer_id' => 'hp_8120e', 'name' => 'HPI297BEA (HP OfficeJet 8120e series)', 'driver' => 'HP',
                    'is_default' => true, 'status' => 'normal', 'shared' => false]],
    'documents' => [['document_id' => 'doc_00000000000000a1', 'name' => 'scan_01M6HWS6TJ6JD4QDGBQ8GP49WB.pdf',
                     'mime' => 'application/pdf', 'bytes' => 9000, 'sha256' => 'x', 'pages' => 1, 'modified_at' => 'Nov 3']]];

function reqTool(World $w, string $name, array $args = [], ?string $token = null): array
{
    $r = tool($w, $name, $args, $token);
    eq(false, $r['isError'], $name . ': ' . ($r['content'][0]['text'] ?? ''));
    return $r['structuredContent'];
}

function deedRequest(World $w, array $extra = []): array
{
    return reqTool($w, 'request_open', $extra + [
        'title' => 'Print the Final PR Deed of Distribution and the Final Affidavit of Identity on the HP OfficeJet 8120e',
        'agents' => ['printer'], 'idempotency_key' => 'deed-and-affidavit',
        'deliverables' => [['label' => 'Final PR Deed of Distribution', 'agent' => 'printer', 'operation' => 'print', 'match' => 'deed'],
                           ['label' => 'Final Affidavit of Identity', 'agent' => 'printer', 'operation' => 'print', 'match' => 'affidavit']]]);
}

function requestAlert(World $w, string $kind, string $requestId): ?array
{
    foreach (alertsNow($w)['alerts'] as $a) {
        if ($a['kind'] === $kind && ($a['details']['request_id'] ?? null) === $requestId) {
            return $a;
        }
    }
    return null;
}

function requestEvents(World $w, string $id): array
{
    $stmt = $w->pdo->prepare('SELECT event FROM request_events WHERE request_id = ? ORDER BY seq');
    $stmt->execute([$id]);
    return $stmt->fetchAll(\PDO::FETCH_COLUMN);
}

/** printer_list once, then the worker answers the discovery with the given outbox. */
function discoverOutbox(World $w, array $details): void
{
    $first = tool($w, 'printer_list')['structuredContent'];
    if (!$first['ready']) {
        list(, $lease) = claimTask($w, $first['task_id']);
        completeTask($w, $first['task_id'], $lease, $details);
    }
}

test('request regression 2026-11-10: two PDFs asked for, none in the outbox, only status checks, no print -- detected, warned, escalated, prerequisite named', function () {
    $w   = alertWorld();
    $req = deedRequest($w);
    $id  = $req['request_id'];
    eq('queued', $req['phase']);
    discoverOutbox($w, SCAN_ONLY_OUTBOX);                  // printer_list #1 (16:01:32), answered by the worker
    $w->clock->t += 31;
    tool($w, 'printer_list');                              // #2
    eq('queued', reqTool($w, 'request_status', ['request_id' => $id])['phase'], 'a status check is not assignment');
    alertsNow($w);
    ok(in_array('not_assigned', requestEvents($w, $id), true), 'not assigned within 30 s is recorded');
    $w->clock->t += 90;                                    // 2 min since the request
    tool($w, 'printer_list');                              // #3: still a check, still not progress
    alertsNow($w);
    ok(in_array('no_progress', requestEvents($w, $id), true), 'no progress at 2 min');
    eq(null, requestAlert($w, 'request_stalled', $id), 'no visible warning before 5 min');
    $w->clock->t += 180;                                   // 5 min
    $a = requestAlert($w, 'request_stalled', $id);
    ok($a !== null, 'a visible warning at 5 min');
    eq('notice', $a['severity']);
    ok(strpos($a['problem'], 'no document matching "deed" is in the print outbox') !== false, 'names the missing deed: ' . $a['problem']);
    ok(strpos($a['problem'], 'no document matching "affidavit" is in the print outbox') !== false, 'names the missing affidavit');
    ok(strpos($a['problem'], 'scan_01M6HWS6TJ6JD4QDGBQ8GP49WB.pdf') !== false, 'says what the outbox does hold');
    ok(strpos($a['problem'], 'Status checks and polls do not count') !== false, 'says checks are not progress');
    $w->clock->t += 300;                                   // 10 min
    $a = requestAlert($w, 'request_stalled', $id);
    eq('escalated', $a['severity'], 'escalated at 10 min');
    $events = requestEvents($w, $id);
    foreach (['opened', 'not_assigned', 'no_progress', 'warning', 'timed_out', 'escalated'] as $e) {
        ok(in_array($e, $events, true), "event $e recorded");
    }
    eq(1, count(array_keys($events, 'escalated')), 'escalated once per stall, however often the watch runs');
    $status = reqTool($w, 'request_status', ['request_id' => $id]);
    eq('open', $status['state']);
    eq('escalated', $status['timer']);
    eq(2, count($status['pending']));
    eq(0, printTasks($w), 'nothing was printed by the supervisor');
    ok(count($status['child_tasks']) >= 1 && $status['child_tasks'][0]['status_check'], 'the discovery is a linked status check');
    // the alert reaches ChatGPT on its next tool result, whatever the tool
    $text = implode("\n", array_column(tool($w, 'printer_list')['content'], 'text'));
    ok(strpos($text, 'BRIDGE ALERTS') !== false && strpos($text, $id) !== false, 'the stalled request rides on the next reply');
});

test('request: a status-check loop with no request is an alert of its own, and opening a request ends it', function () {
    $w = alertWorld();
    discoverOutbox($w, SCAN_ONLY_OUTBOX);
    tool($w, 'printer_list');
    eq(null, alertOf(alertsNow($w), 'status_loop'), 'two checks are not a loop');
    tool($w, 'printer_list_documents');
    $a = alertOf(alertsNow($w), 'status_loop');
    ok($a !== null, 'three checks and no action is a loop');
    ok(strpos($a['problem'], 'without submitting anything or opening a request') !== false, $a['problem']);
    deedRequest($w);
    eq(null, alertOf(alertsNow($w), 'status_loop'), 'an open request accounts for the checks');
});

test('request: a declared blocker is visible at once, owned, escalates, and clearing it resets the timer', function () {
    $w   = alertWorld();
    $req = deedRequest($w, ['blocked_on' => 'Both documents are drafts (deed: "DRAFT - FOR REVIEW ONLY"); the final PDFs are not in the print outbox',
                            'blocked_owner' => 'EG']);
    eq('blocked', $req['phase']);
    eq('EG', $req['responsible']);
    $a = requestAlert($w, 'request_blocked', $req['request_id']);
    ok($a !== null && strpos($a['problem'], 'waiting on EG') !== false, 'blocked alert names the owner');
    ok(strpos($a['recommended_action'], 'Never print or resend') !== false, 'never print to clear it');
    $w->clock->t += 600;
    eq('escalated', requestAlert($w, 'request_blocked', $req['request_id'])['severity']);
    $after = reqTool($w, 'request_update', ['request_id' => $req['request_id'], 'clear_blocker' => true]);
    eq('queued', $after['phase']);
    eq(0, $after['since_progress_seconds']);
    eq(null, requestAlert($w, 'request_blocked', $req['request_id']), 'the blocked alert clears with the blocker');
    eq(0, printTasks($w));
});

test('request: completes only on spooler evidence, links the job to the request, and never claims paper', function () {
    $w   = alertWorld();
    $req = reqTool($w, 'request_open', ['title' => 'Print the notary plan', 'agents' => ['printer'],
        'deliverables' => [['label' => 'Deed Notary Plan', 'agent' => 'printer', 'operation' => 'print', 'match' => 'notary plan']]]);
    $id  = $req['request_id'];
    $job = queuePrint($w);
    $st  = reqTool($w, 'request_status', ['request_id' => $id]);
    eq('waiting', $st['phase']);
    ok(strpos($st['responsible'], $job) !== false, 'the printer worker holding the job is responsible');
    eq($job, $st['deliverables'][0]['task_id'], 'the downstream job is linked to the original request');
    list(, $lease) = claimTask($w, $job);
    completeTask($w, $job, $lease, ['printer' => 'print', 'job' => ['job_id' => $job, 'state' => 'submitted_to_spooler']]);
    alertsNow($w);
    $st = reqTool($w, 'request_status', ['request_id' => $id]);
    eq('open', $st['state'], 'handed to the spooler is not done');
    eq('submitted', $st['deliverables'][0]['status']);
    $refused = tool($w, 'request_close', ['request_id' => $id, 'outcome' => 'completed', 'reason' => 'sent it']);
    eq(true, $refused['isError']);
    ok(strpos($refused['content'][0]['text'], 'never completed because something was submitted') !== false, 'refusal says why');
    $check = tool($w, 'printer_get_status', ['job_id' => $job, 'refresh' => true])['structuredContent']['check_task_id'];
    list(, $lease) = claimTask($w, $check);
    completeTask($w, $check, $lease, ['printer' => 'status', 'job' => ['job_id' => $job, 'state' => 'completed_per_spooler']]);
    alertsNow($w);
    $st = reqTool($w, 'request_status', ['request_id' => $id]);
    eq('completed', $st['state']);
    ok(strpos($st['completion_evidence'][0], 'paper output not verified') !== false, 'evidence never claims paper');
    ok(strpos($st['completion_evidence'][0], $job) !== false, 'evidence names the job');
    eq(1, printTasks($w), 'exactly one print');
});

test('request: a second print of the same document is refused; a replay of the same key is not', function () {
    $w   = alertWorld();
    reqTool($w, 'request_open', ['title' => 'Print the plan', 'agents' => ['printer']]);
    $args = ['text' => 'Deed Notary Plan', 'title' => 'Deed Notary Plan', 'confirm' => true, 'idempotency_key' => 'plan-1'];
    $first = tool($w, 'printer_print_text', $args)['structuredContent']['job_id'];
    $replay = tool($w, 'printer_print_text', $args)['structuredContent'];
    eq($first, $replay['job_id']);
    eq(true, $replay['replayed']);
    $dup = tool($w, 'printer_print_text', ['idempotency_key' => 'plan-2'] + $args);
    eq(true, $dup['isError']);
    ok(strpos($dup['content'][0]['text'], 'duplicate_print') !== false, $dup['content'][0]['text']);
    eq(1, printTasks($w));
});

test('request: a crashed worker is recovered by the bounded retry and the recovery is recorded', function () {
    $w   = alertWorld();
    $req = reqTool($w, 'request_open', ['title' => 'Explain the watch', 'agents' => ['codex'],
        'deliverables' => [['label' => 'explanation', 'agent' => 'codex', 'operation' => 'explain']]]);
    $id  = $w->submit(['operation' => 'explain'])->json['task_id'];
    claimTask($w, $id);
    $w->clock->t += 61;                                    // the lease (60 s) lapses: the worker crashed
    poll($w);                                              // the queue requeues it (attempt 2 of 2)
    list(, $lease) = claimTask($w, $id);
    alertsNow($w);
    $st = reqTool($w, 'request_status', ['request_id' => $req['request_id']]);
    eq(1, $st['recovery_attempts']);
    eq('running', $st['phase']);
    eq(200, $w->call('POST', "/worker/tasks/$id/complete", $w->worker,
                     ['lease_token' => $lease, 'ok' => true, 'summary' => 'explained', 'body' => 'x'])->status);
    alertsNow($w);
    eq('completed', reqTool($w, 'request_status', ['request_id' => $req['request_id']])['state']);
});

test('request: a task queued only on the gateway (no worker) is waiting, stalls visibly, and says not to resubmit', function () {
    $w   = alertWorld();
    $req = reqTool($w, 'request_open', ['title' => 'Inspect the repo', 'agents' => ['codex']]);
    $task = $w->submit()->json['task_id'];
    $st  = reqTool($w, 'request_status', ['request_id' => $req['request_id']]);
    eq('waiting', $st['phase']);
    eq($task, $st['child_tasks'][0]['task_id']);
    $w->clock->t += 300;
    $a = requestAlert($w, 'request_stalled', $req['request_id']);
    ok($a !== null && strpos($a['recommended_action'], 'Do not resubmit') !== false, 'waiting request warns without resubmitting');
});

test('request: real progress resets the timer; a repeated note, a poll or a status read does not', function () {
    $w   = alertWorld();
    $req = reqTool($w, 'request_open', ['title' => 'Prepare the affidavit', 'agents' => ['codex']]);
    $id  = $req['request_id'];
    $w->clock->t += 200;
    reqTool($w, 'request_update', ['request_id' => $id, 'progress' => 'checked the plat reference']);
    $w->clock->t += 200;
    reqTool($w, 'request_update', ['request_id' => $id, 'progress' => 'checked the plat reference']);   // same note: a poll
    reqTool($w, 'request_status', ['request_id' => $id]);
    poll($w);
    eq(200, reqTool($w, 'request_status', ['request_id' => $id])['since_progress_seconds']);
    list($task, $lease) = $w->running();
    $w->clock->t += 50;                                    // inside the 60 s lease
    eq(200, $w->call('POST', "/worker/tasks/$task/events", $w->worker,
                     ['lease_token' => $lease, 'kind' => 'progress', 'message' => 'page 2 of 3'])->status);
    alertsNow($w);
    eq(0, reqTool($w, 'request_status', ['request_id' => $id])['since_progress_seconds'], 'a worker event is progress');
});

test('request: history and timers survive a supervisor restart without repeating events', function () {
    $w   = alertWorld();
    $req = deedRequest($w);
    $w->clock->t += 301;
    alertsNow($w);
    $before = requestEvents($w, $req['request_id']);
    // a new gateway process over the same database: everything it knows comes from the tables
    $w->app = Bootstrap::build(['database' => ':memory:', 'lease_seconds' => 60, 'max_attempts' => 2, 'max_body' => 65536,
        'allowed_operations' => ['inspect', 'explain', 'search_memory', 'print', 'print_cancel', 'print_status', 'print_discover'],
        'allowed_modes' => ['read_only', 'device_control'], 'memory_agent' => 'codex', 'memory_workspace' => 'agent_blocks',
        'printer_scopes' => ['test client' => \AgentBridge\PrinterScopes::ALL], 'alert_viewers' => ['test client']],
        $w->pdo, $w->clock, new FakeIds());
    alertsNow($w);
    eq($before, requestEvents($w, $req['request_id']), 'no event repeated after the restart');
    $w->clock->t += 300;
    eq('escalated', requestAlert($w, 'request_stalled', $req['request_id'])['severity'], 'the 10-minute escalation still comes');
    eq('open', reqTool($w, 'request_status', ['request_id' => $req['request_id']])['state']);
});

test('request: open is idempotent, close needs a reason, other clients cannot see it, and a closed request stays closed', function () {
    $w   = alertWorld();
    $a   = deedRequest($w);
    $b   = deedRequest($w);
    eq($a['request_id'], $b['request_id']);
    eq(true, $b['replayed']);
    eq(true, tool($w, 'request_close', ['request_id' => $a['request_id'], 'outcome' => 'cancelled', 'reason' => ''])['isError']);
    $closed = reqTool($w, 'request_close', ['request_id' => $a['request_id'], 'outcome' => 'cancelled',
                                            'reason' => 'EG: the documents are still drafts']);
    eq('cancelled', $closed['state']);
    eq(true, tool($w, 'request_update', ['request_id' => $a['request_id'], 'progress' => 'x'])['isError']);
    eq(null, requestAlert($w, 'request_stalled', $a['request_id']));
    ok(!in_array('request_open', toolNames($w, $w->client2), true), 'request tools are EG\'s identities only');
    $snap = alertsNow($w);
    eq($a['request_id'], $snap['requests']['recently_closed'][0]['request_id'], 'the dashboard sees it closed');
    eq(300, $snap['requests']['thresholds']['warn_seconds']);
});

test('request policy: thresholds are configurable and status operations never count as progress', function () {
    $p = new RequestPolicy(['warn_seconds' => 60, 'escalate_seconds' => 120]);
    $req = ['id' => 'rq_x', 'title' => 't', 'client_label' => 'c', 'state' => 'open', 'phase' => 'queued', 'blocked_on' => null,
            'blocked_owner' => null, 'progress_note' => null, 'created_at' => 0, 'last_progress_at' => 0, 'deliverables' => '[]'];
    $check = ['id' => 't1', 'agent' => 'printer', 'operation' => 'print_discover', 'state' => 'succeeded', 'attempts' => 1,
              'max_attempts' => 2, 'prompt' => 'discover', 'created_at' => 100, 'last_event_at' => 100, 'last_error' => null];
    $a = $p->assess($req, [$check], null, 130);
    eq(0, $a['progress_at']);
    eq('escalated', $a['timer']);
    eq(2, $a['candidates']['request_stalled:rq_x']['level']);
});

test('request REST: Toyota opens, blocks, reads and closes a request without MCP; a worker token cannot', function () {
    $w  = alertWorld();
    $r  = $w->call('POST', '/requests', $w->client, ['title' => 'Scan the receipts', 'agents' => ['codex'],
                                                    'blocked_on' => 'EG has not put them on the scanner', 'blocked_owner' => 'EG']);
    eq(201, $r->status);
    $id = $r->json['request_id'];
    eq('blocked', $w->call('GET', "/requests/$id", $w->client)->json['phase']);
    eq(200, $w->call('POST', "/requests/$id/update", $w->client, ['clear_blocker' => true])->status);
    eq(1, count($w->call('GET', '/requests', $w->client)->json['requests']));
    eq('cancelled', $w->call('POST', "/requests/$id/close", $w->client, ['outcome' => 'cancelled', 'reason' => 'EG changed his mind'])->json['state']);
    eq(403, $w->call('GET', '/requests', $w->worker)->status);
    eq(403, $w->call('GET', '/requests', $w->client2)->status);
    eq(405, $w->call('DELETE', "/requests/$id", $w->client)->status);
});
