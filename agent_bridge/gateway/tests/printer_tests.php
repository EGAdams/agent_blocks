<?php
declare(strict_types=1);

// The printer bridge: printer_* MCP tools and PrintGuard. Required by
// run_tests.php; uses its World, test(), eq() and ok(). No printer is touched:
// the worker side is played by claim/complete calls.

namespace AgentBridge\Tests;

use AgentBridge\Principal;
use AgentBridge\PrinterScopes;
use AgentBridge\PrintPromptShape;

/** Printing switched on, 'test client' holding every printer scope, 'other client' none. */
function printWorld(array $overrides = []): World
{
    return new World($overrides + [
        'allowed_operations' => ['inspect', 'explain', 'search_memory', 'print', 'print_cancel', 'print_status', 'print_discover'],
        'allowed_modes'      => ['read_only', 'device_control'],
        'printer_scopes'     => ['test client' => PrinterScopes::ALL],
        'printer_max_prints_per_hour' => 4,
        'printer_max_open'   => 2,
        'agents' => [
            ['name' => 'codex', 'display_name' => 'Codex', 'description' => 'reads code',
             'operations' => ['inspect', 'explain', 'search_memory'], 'workspaces' => ['agent_blocks']],
            ['name' => 'printer', 'display_name' => 'Printer', 'description' => 'EG\'s printers',
             'operations' => ['print', 'print_cancel', 'print_status', 'print_discover'], 'workspaces' => ['printers']],
        ],
    ]);
}

function printEnvelope(string $operation, string $prompt, array $extra = []): array
{
    return $extra + ['schema_version' => '1.0', 'task_type' => 'agent_task', 'agent' => 'printer', 'workspace' => 'printers',
                     'operation' => $operation, 'approval_mode' => in_array($operation, ['print', 'print_cancel'], true)
                         ? 'device_control' : 'read_only', 'prompt' => $prompt];
}

function printTasks(World $w, string $operation = 'print'): int
{
    $stmt = $w->pdo->prepare('SELECT COUNT(*) FROM tasks WHERE operation = ?');
    $stmt->execute([$operation]);
    return (int) $stmt->fetchColumn();
}

/** Claims a task as the worker; returns [prompt, lease_token]. */
function claimTask(World $w, string $id): array
{
    $claim = $w->call('POST', "/worker/tasks/$id/claim", $w->worker, ['worker_id' => 'w1']);
    eq(200, $claim->status, 'claim');
    return [$claim->json['prompt'], $claim->json['lease_token']];
}

function completeTask(World $w, string $id, string $lease, array $details, bool $ok = true): void
{
    eq(200, $w->call('POST', "/worker/tasks/$id/complete", $w->worker,
                     ['lease_token' => $lease, 'ok' => $ok, 'summary' => 'done', 'body' => 'report',
                      'performer' => 'printer', 'details' => $details])->status, 'complete');
}

const DISCOVERED = ['printer' => 'discover', 'default' => 'hp_8120e', 'recent' => [],
    'printers' => [['printer_id' => 'hp_8120e', 'name' => 'HPI297BEA (HP OfficeJet 8120e series)', 'driver' => 'HP',
                    'is_default' => true, 'status' => 'normal', 'shared' => false],
                   ['printer_id' => 'hp_4100', 'name' => 'HP063E28 (HP DeskJet 4100 series)', 'driver' => 'HP',
                    'is_default' => false, 'status' => 'normal', 'shared' => false]],
    'documents' => [['document_id' => 'doc_0123456789abcdef', 'name' => 'letter.pdf', 'mime' => 'application/pdf',
                     'bytes' => 1234, 'sha256' => 'x', 'pages' => 2, 'modified_at' => 'now']]];

test('print: scopes are the union of id, label and *, and nothing for a worker', function () {
    $s = new PrinterScopes(['c1' => ['printer:list'], 'phone' => ['printer:status'], '*' => ['printer:cancel', 'bogus']]);
    eq(['printer:list', 'printer:status', 'printer:cancel'], $s->of(new Principal('c1', 'client', 'phone')));
    eq([], (new PrinterScopes([]))->of(new Principal('c1', 'client', 'phone')));
    eq([], $s->of(new Principal('c1', 'worker', 'phone')));
});

test('print: off by default — no print tools, and the REST route refuses a print', function () {
    $w = new World(['printer_scopes' => ['test client' => PrinterScopes::ALL],
                    'agents' => [['name' => 'printer', 'display_name' => 'P', 'description' => 'd',
                                  'operations' => ['print'], 'workspaces' => ['printers']]]]);
    $names = toolNames($w);
    ok(!in_array('printer_print_text', $names, true), 'print tool offered while printing is disabled');
    ok(!in_array('printer_cancel', $names, true), 'cancel offered while printing is disabled');
    ok(in_array('printer_get_status', $names, true), 'read tools should still follow scopes');
    eq(403, $w->submit(printEnvelope('print', '{"kind":"test_page"}'))->status);
    eq(true, tool($w, 'printer_print_text', ['test_page' => true, 'confirm' => true])['isError']);
    eq(0, printTasks($w));
});

test('print: tools are listed only to a client holding their scope, and never via submit_agent_task', function () {
    $w = printWorld();
    foreach (array_keys(\AgentBridge\PrinterTools::TOOLS) as $name) {
        ok(in_array($name, toolNames($w), true), "$name missing");
    }
    eq([], array_values(array_filter(toolNames($w, $w->client2), function ($n) { return strpos($n, 'printer_') === 0; })));
    eq(true, tool($w, 'printer_print_text', ['text' => 'hi', 'confirm' => true], $w->client2)['isError']);
    $submit = $w->mcp('tools/list')->json['result']['tools'][0];
    foreach (['print', 'print_cancel', 'print_status', 'print_discover'] as $op) {
        ok(!in_array($op, $submit['inputSchema']['properties']['operation']['enum'], true), "submit_agent_task offers $op");
        eq(true, tool($w, 'submit_agent_task', ['agent' => 'printer', 'workspace' => 'printers', 'operation' => $op,
                                                'prompt' => 'discover'])['isError'], $op);
    }
    ok(strpos($w->mcp('initialize', [])->json['result']['instructions'], 'printer_*') !== false, 'no printer note');
    ok(strpos($w->mcp('initialize', [], 1, $w->client2)->json['result']['instructions'], 'printer') === false,
       'printer note shown to a client without printer scopes');
    eq(0, printTasks($w));
});

test('print: nothing prints without confirm=true', function () {
    $w = printWorld();
    foreach ([[], ['confirm' => false], ['confirm' => 'true'], ['confirm' => 1]] as $extra) {
        $r = tool($w, 'printer_print_text', ['text' => 'hello'] + $extra);
        eq(true, $r['isError']);
        ok(strpos($r['content'][0]['text'], 'confirmation_required') === 0, 'wrong refusal');
    }
    eq(true, tool($w, 'printer_print_document', ['document_id' => 'doc_0123456789abcdef'])['isError']);
    eq(0, printTasks($w));
});

test('print: bad printer, document, options and oversize text are refused before queueing', function () {
    $w = printWorld();
    $bad = [
        ['text' => 'x', 'printer_id' => '../etc', 'confirm' => true],
        ['text' => 'x', 'printer_id' => 'HP; del *', 'confirm' => true],
        ['text' => 'x', 'copies' => 11, 'confirm' => true],
        ['text' => 'x', 'copies' => '2', 'confirm' => true],
        ['text' => 'x', 'page_range' => '1-3;rm', 'confirm' => true],
        ['text' => 'x', 'orientation' => 'sideways', 'confirm' => true],
        ['text' => str_repeat('a', 6001), 'confirm' => true],
        ['text' => '   ', 'confirm' => true],
        ['text' => 'x', 'title' => str_repeat('t', 121), 'confirm' => true],
    ];
    foreach ($bad as $args) {
        eq(true, tool($w, 'printer_print_text', $args)['isError'], json_encode(array_keys($args)));
    }
    foreach (['C:\\Users\\x.pdf', '/etc/passwd', 'doc_123', 'doc_0123456789ABCDEF'] as $doc) {
        eq(true, tool($w, 'printer_print_document', ['document_id' => $doc, 'confirm' => true])['isError'], $doc);
    }
    eq(0, printTasks($w));
});

test('print: the REST route checks the prompt shape and the scopes too', function () {
    $w = printWorld();
    foreach (['{"kind":"text","text":"x","path":"/etc/passwd"}', '{"kind":"shell"}', 'notepad.exe /p x', '[]',
              '{"kind":"text","text":"x","options":{"copies":99}}'] as $prompt) {
        eq(403, $w->submit(printEnvelope('print', $prompt))->status, $prompt);
    }
    foreach (['discover; rm', 'job=t_1', 'recent jobs'] as $prompt) {
        eq(403, $w->submit(printEnvelope('print_status', $prompt))->status, $prompt);
    }
    eq(403, $w->submit(printEnvelope('print', '{"kind":"test_page"}'), $w->client2)->status, 'no printer:print scope');
    eq(403, $w->submit(printEnvelope('print', '{"kind":"test_page"}', ['approval_mode' => 'read_only']))->status);
    eq(null, PrintPromptShape::problem('print', '{"kind":"text","text":"Ünïcødé ✓","printer_id":"hp_4100",'
                                                . '"options":{"copies":2,"page_range":"1-3,5","orientation":"landscape"}}'));
    eq(0, printTasks($w));
});

test('print: a print queues once with a JSON prompt; a duplicate and a replay are told apart', function () {
    $w   = printWorld();
    $one = tool($w, 'printer_print_text', ['text' => 'Ünïcødé line', 'title' => 'Note', 'copies' => 2,
                                           'idempotency_key' => 'gpt-1', 'confirm' => true]);
    eq(false, $one['isError'], $one['content'][0]['text']);
    $job = $one['structuredContent'];
    eq('accepted', $job['state']);
    eq(false, $job['physical_output_verified']);
    $again = tool($w, 'printer_print_text', ['text' => 'Ünïcødé line', 'title' => 'Note', 'copies' => 2,
                                             'idempotency_key' => 'gpt-1', 'confirm' => true])['structuredContent'];
    eq($job['job_id'], $again['job_id'], 'a replay returns the same job');
    eq(true, $again['replayed']);
    $dup = tool($w, 'printer_print_text', ['text' => 'Ünïcødé line', 'title' => 'Note', 'copies' => 2, 'confirm' => true]);
    eq(true, $dup['isError']);
    ok(strpos($dup['content'][0]['text'], 'duplicate_print') === 0, $dup['content'][0]['text']);
    eq(1, printTasks($w));
    list($prompt) = claimTask($w, $job['job_id']);
    eq(['kind' => 'text', 'text' => 'Ünïcødé line', 'title' => 'Note', 'options' => ['copies' => 2]],
       json_decode($prompt, true));
    $w->clock->t += 601;
    eq(false, tool($w, 'printer_print_text', ['text' => 'Ünïcødé line', 'title' => 'Note', 'copies' => 2,
                                              'confirm' => true])['isError'], 'the same text again after ten minutes');
});

test('print: at most printer_max_open unfinished, and printer_max_prints_per_hour an hour', function () {
    $w = printWorld();
    eq(false, tool($w, 'printer_print_text', ['text' => 'a', 'confirm' => true])['isError']);
    eq(false, tool($w, 'printer_print_text', ['text' => 'b', 'confirm' => true])['isError']);
    $busy = tool($w, 'printer_print_text', ['text' => 'c', 'confirm' => true]);
    ok(strpos($busy['content'][0]['text'], 'printer_busy') === 0, $busy['content'][0]['text']);
    foreach ($w->pdo->query("SELECT id FROM tasks WHERE operation = 'print'")->fetchAll(\PDO::FETCH_COLUMN) as $id) {
        list(, $lease) = claimTask($w, $id);
        completeTask($w, $id, $lease, ['printer' => 'print', 'job' => ['state' => 'completed_per_spooler']]);
    }
    eq(false, tool($w, 'printer_print_text', ['text' => 'c', 'confirm' => true])['isError']);
    eq(false, tool($w, 'printer_print_text', ['text' => 'd', 'confirm' => true])['isError']);
    foreach ($w->pdo->query("SELECT id FROM tasks WHERE operation = 'print' AND state = 'queued'")->fetchAll(\PDO::FETCH_COLUMN) as $id) {
        list(, $lease) = claimTask($w, $id);
        completeTask($w, $id, $lease, ['printer' => 'print', 'job' => ['state' => 'completed_per_spooler']]);
    }
    $limited = tool($w, 'printer_print_text', ['text' => 'e', 'confirm' => true]);
    ok(strpos($limited['content'][0]['text'], 'rate_limited') === 0, $limited['content'][0]['text']);
    eq(4, printTasks($w));
});

test('print: discovery is cached ten minutes, pending while asked, and refreshable — and never prints', function () {
    $w     = printWorld();
    $first = tool($w, 'printer_list')['structuredContent'];
    eq(false, $first['ready']);
    $again = tool($w, 'printer_get_default')['structuredContent'];
    eq($first['task_id'], $again['task_id'], 'a pending discover is reused');
    eq(1, printTasks($w, 'print_discover'));
    list($prompt, $lease) = claimTask($w, $first['task_id']);
    eq('discover', $prompt);
    completeTask($w, $first['task_id'], $lease, DISCOVERED);
    $list = tool($w, 'printer_list')['structuredContent'];
    eq(true, $list['ready']);
    eq('hp_8120e', $list['default']);
    eq(['hp_8120e', 'hp_4100'], array_column($list['printers'], 'printer_id'));
    eq('HPI297BEA (HP OfficeJet 8120e series)', tool($w, 'printer_get_default')['structuredContent']['printer']['name']);
    $docs = tool($w, 'printer_list_documents')['structuredContent']['documents'];
    eq('doc_0123456789abcdef', $docs[0]['document_id']);
    ok(!isset($docs[0]['sha256']), 'documents expose only what a caller needs');
    eq(1, printTasks($w, 'print_discover'), 'a fresh answer is reused');
    eq(false, tool($w, 'printer_list', ['refresh' => true])['structuredContent']['ready']);
    eq(2, printTasks($w, 'print_discover'));
    eq(0, printTasks($w));
});

test('print: round trip — print, worker claims, completes, status shows the spooler state but never paper', function () {
    $w   = printWorld();
    $job = tool($w, 'printer_print_text', ['test_page' => true, 'confirm' => true])['structuredContent'];
    $st  = tool($w, 'printer_get_status', ['job_id' => $job['job_id'], 'refresh' => true])['structuredContent'];
    eq('queued', $st['state']);
    eq(false, $st['checking'], 'a queued print gets no status task');
    eq(0, printTasks($w, 'print_status'));
    list($prompt, $lease) = claimTask($w, $job['job_id']);
    eq('{"kind":"test_page"}', $prompt);
    eq('submitting', tool($w, 'printer_get_status', ['job_id' => $job['job_id']])['structuredContent']['state']);
    completeTask($w, $job['job_id'], $lease, ['printer' => 'print', 'job' => [
        'job_id' => $job['job_id'], 'state' => 'submitted_to_spooler', 'title' => 'STAR TREK BRIDGE — PRINT TEST',
        'printer' => 'HPI297BEA (HP OfficeJet 8120e series)', 'detail' => 'Windows job 41']]);
    $st = tool($w, 'printer_get_status', ['job_id' => $job['job_id']])['structuredContent'];
    eq('submitted_to_spooler', $st['state']);
    eq('STAR TREK BRIDGE — PRINT TEST', $st['title']);
    eq(false, $st['physical_output_verified']);
    $st = tool($w, 'printer_get_status', ['job_id' => $job['job_id'], 'refresh' => true])['structuredContent'];
    eq(true, $st['checking']);
    $check = $st['check_task_id'];
    eq($check, tool($w, 'printer_get_status', ['job_id' => $job['job_id'], 'refresh' => true])['structuredContent']['check_task_id'],
       'a pending status check is reused');
    eq(1, printTasks($w, 'print_status'));
    list($prompt, $lease) = claimTask($w, $check);
    eq('job=' . $job['job_id'], $prompt);
    completeTask($w, $check, $lease, ['printer' => 'status', 'job' => ['state' => 'completed_per_spooler']]);
    $st = tool($w, 'printer_get_status', ['job_id' => $job['job_id']])['structuredContent'];
    eq('completed_per_spooler', $st['state']);
    eq(false, $st['physical_output_verified']);
    ok(strpos($st['note'], 'not that EG holds the page') !== false, 'no paper caveat');
    $recent = tool($w, 'printer_list_recent')['structuredContent']['jobs'];
    eq($job['job_id'], $recent[0]['job_id']);
    ok(strpos(json_encode($recent), '"text"') === false, 'recent jobs never carry the text');
    eq(1, printTasks($w), 'status checks never print');
    eq(true, tool($w, 'printer_get_status', ['job_id' => $check])['isError'], 'a status task is not a print job');
    eq(true, tool($w, 'printer_get_status', ['job_id' => $job['job_id']], $w->client2)['isError'], 'another client');
});

test('print: cancel — queued is cancelled here; claimed asks the machine once; ended is refused', function () {
    $w = printWorld();
    $a = tool($w, 'printer_print_text', ['text' => 'a', 'confirm' => true])['structuredContent']['job_id'];
    eq(true, tool($w, 'printer_cancel', ['job_id' => $a])['isError'], 'cancel needs confirm');
    $out = tool($w, 'printer_cancel', ['job_id' => $a, 'confirm' => true])['structuredContent'];
    eq('canceled', $out['state']);
    eq(0, printTasks($w, 'print_cancel'));
    eq(true, tool($w, 'printer_cancel', ['job_id' => $a, 'confirm' => true])['isError'], 'already cancelled');
    $b = tool($w, 'printer_print_text', ['text' => 'b', 'confirm' => true])['structuredContent']['job_id'];
    claimTask($w, $b);
    $out = tool($w, 'printer_cancel', ['job_id' => $b, 'confirm' => true])['structuredContent'];
    eq('cancel_requested', $out['state']);
    $again = tool($w, 'printer_cancel', ['job_id' => $b, 'confirm' => true])['structuredContent'];
    eq($out['cancel_task_id'], $again['cancel_task_id'], 'a second cancel replays the first');
    eq(1, printTasks($w, 'print_cancel'));
    list($prompt) = claimTask($w, $out['cancel_task_id']);
    eq('cancel=' . $b, $prompt);
    eq(true, tool($w, 'printer_cancel', ['job_id' => 't_000000000000000000000fff', 'confirm' => true])['isError']);
    eq(2, printTasks($w), 'cancelling never prints');
});
