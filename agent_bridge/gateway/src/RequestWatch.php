<?php
declare(strict_types=1);

namespace AgentBridge;

use PDO;

/*
 * Request supervision: the user's request, watched end to end.
 *
 * On 2026-11-10 EG asked ChatGPT to print the final deed and the affidavit.
 * ChatGPT made three printer_list checks (16:01, 16:16, 16:17 local); each
 * succeeded, no print task was ever submitted, and no watch said a word,
 * because every watch -- the gateway's AlertWatch, the box's worker supervisor,
 * factory-ops-watch -- looked at tasks, and no task was stuck. The request
 * itself lived only in a chat. This makes it a record the gateway holds:
 *
 *   RequestPolicy        pure: a request, its linked tasks and the outbox -> phase,
 *                        progress, pending prerequisites, events, alert candidates
 *   SqliteRequestStore   requests, request_tasks, request_events, client_calls (IRequestStore)
 *   RequestWatch         open/update/close/status/list, evaluate() for AlertWatch's
 *                        refresh, and the status-loop check
 *   GuardRequestDuplicatesPhase  a submit phase: a second print of a document the
 *                        request already printed, or is printing, is refused
 *   LinkRequestPhase     a submit phase: each new task joins its client's open request
 *   RequestTools         request_open / request_update / request_status /
 *                        request_list / request_close (IToolFamily)
 *
 * The rules it enforces, in code:
 *  - a request never leaves supervision until it is completed with evidence, or
 *    failed or cancelled with a reason;
 *  - polling, status checks (operations named in check_operations) and empty
 *    heartbeats never count as progress; a worker's task events and a client's
 *    request_update with a new progress note do;
 *  - "submitted to another agent" is never completion: a print deliverable is met
 *    only when Windows' spooler reports the job completed, and even then the
 *    evidence says paper output was not verified;
 *  - nothing here prints, reprints, retries or cancels: a lapsed lease is retried
 *    by the queue's own bounded attempts, and that retry is recorded as a recovery.
 */

interface IRequestStore
{
    public function insert(array $row): array;
    public function find(string $id): ?array;
    public function findByKey(string $clientId, string $key): ?array;
    /** @return array[] open requests, oldest first */
    public function openRequests(): array;
    /** @return array[] requests closed since $since, newest first */
    public function closedSince(int $since): array;
    /** @return array[] every request of a client (or all when null), newest first */
    public function listFor(?string $clientId, bool $includeClosed): array;
    public function newestOpenFor(string $clientId, string $agent): ?array;
    public function set(string $id, array $fields): void;
    public function link(string $requestId, string $taskId, int $now): bool;
    /** @return array[] linked tasks with result, latest print_status check, and latest event time */
    public function tasks(string $requestId): array;
    /** Records an event once per dedupe; returns true when it was new. */
    public function event(string $requestId, string $event, ?string $dedupe, array $detail, int $now): bool;
    /** @return array[] newest last */
    public function events(string $requestId, int $limit = 50): array;
    /** @return array{at:int,documents:array}|null the newest successful print_discover result */
    public function outbox(): ?array;
    public function recordCall(string $clientId, string $label, string $tool, int $now): void;
    /** @return array[] client_id, client_label, tool, at -- since $since */
    public function callsSince(int $since): array;
}

/** Pure: what a request's state is, given its row, its linked tasks and the latest outbox listing. */
final class RequestPolicy
{
    const STALL_KINDS = ['request_stalled', 'request_blocked'];

    /** @var array */ private $t;

    public function __construct(array $thresholds = [])
    {
        $this->t = $thresholds + self::defaults();
    }

    public static function defaults(): array
    {
        return ['accept_seconds' => 30, 'progress_seconds' => 120, 'warn_seconds' => 300, 'escalate_seconds' => 600,
                'loop_window_seconds' => 1200, 'loop_checks' => 3,
                'check_operations' => ['print_status', 'print_discover', 'search_memory'],
                'check_tools' => ['printer_list', 'printer_get_default', 'printer_list_documents', 'printer_get_status',
                                  'printer_list_recent', 'get_agent_task_status', 'get_agent_task_result',
                                  'list_available_agents', 'bridge_alerts', 'request_status', 'request_list',
                                  'browser_list_tabs', 'notes_list']];
    }

    public function threshold(string $name): int
    {
        return (int) $this->t[$name];
    }

    public function isCheck(string $operation): bool
    {
        return in_array($operation, (array) $this->t['check_operations'], true);
    }

    /**
     * @return array phase, progress_at, responsible, pending[], deliverables[], complete, evidence[],
     *               events[] ([event, dedupe, detail]), candidates (key => candidate), timer
     */
    public function assess(array $req, array $tasks, ?array $outbox, int $now): array
    {
        $work     = array_values(array_filter($tasks, function ($t) { return !$this->isCheck((string) $t['operation']); }));
        $progress = (int) $req['last_progress_at'];
        foreach ($work as $t) {
            $progress = max($progress, (int) $t['created_at'], (int) ($t['last_event_at'] ?? 0));
        }
        $deliver  = [];
        $pending  = [];
        $events   = [];
        $failedDeliverable = null;
        foreach (self::json($req['deliverables']) as $d) {
            $row = $this->deliverable($d, $tasks, $outbox);
            $deliver[] = $row;
            if ($row['status'] === 'met') {
                $events[] = ['deliverable_met', 'deliverable_met@' . $row['key'], ['deliverable' => $row['label'],
                             'task_id' => $row['task_id'], 'evidence' => $row['evidence']]];
            } else {
                $pending[] = $row['label'] . ': ' . $row['why'];
                if (in_array($row['status'], ['failed', 'unconfirmed'], true) && $failedDeliverable === null) {
                    $failedDeliverable = $row;
                }
            }
        }
        foreach ($work as $t) {
            if ((int) $t['attempts'] > 1) {
                $events[] = ['recovery', 'recovery@' . $t['id'] . '#' . (int) $t['attempts'],
                             ['task_id' => $t['id'], 'attempt' => (int) $t['attempts'], 'of' => (int) $t['max_attempts'],
                              'how' => 'its worker stopped reporting; the lease expired and the queue handed it out again']];
            }
        }
        $active   = null;
        foreach ($work as $t) {
            if (in_array($t['state'], [TaskState::QUEUED, TaskState::RUNNING], true)
                && ($active === null || $t['state'] === TaskState::RUNNING)) {
                $active = $t;
            }
        }
        $complete = $deliver !== [] && count(array_filter($deliver, function ($d) { return $d['status'] === 'met'; })) === count($deliver);
        $blocker  = trim((string) $req['blocked_on']);
        if ($req['state'] !== 'open') {
            $phase = $req['state'];
        } elseif ($complete) {
            $phase = 'completed';
        } elseif ($blocker !== '' || $failedDeliverable !== null) {
            $phase = 'blocked';
        } elseif ($active !== null) {
            $phase = $active['state'] === TaskState::RUNNING ? 'running' : 'waiting';
        } else {
            $phase = $work === [] && trim((string) $req['progress_note']) === '' ? 'queued' : 'running';
        }
        if ($blocker === '' && $failedDeliverable !== null) {
            $blocker = $failedDeliverable['label'] . ': ' . $failedDeliverable['why'];
        }
        $responsible = $phase === 'blocked' && trim((string) $req['blocked_owner']) !== '' ? (string) $req['blocked_owner']
                     : ($active !== null ? 'the ' . $active['agent'] . ' worker (task ' . $active['id'] . ', ' . $active['state'] . ')'
                                         : (string) $req['client_label']);
        $out = ['phase' => $phase, 'progress_at' => $progress, 'responsible' => $responsible, 'blocked_on' => $blocker,
                'pending' => $pending, 'deliverables' => $deliver, 'complete' => $complete,
                'evidence' => array_values(array_map(function ($d) { return $d['label'] . ': ' . $d['evidence']; },
                                                     array_filter($deliver, function ($d) { return $d['status'] === 'met'; }))),
                'events' => $events, 'candidates' => [], 'timer' => 'ok', 'stalled_seconds' => $now - $progress,
                'deadline_at' => $progress + $this->threshold('escalate_seconds')];
        if ($req['state'] !== 'open' || $complete) {
            $out['timer'] = 'closed';
            return $out;
        }
        $this->timers($req, $out, $work === [], $active, $now);
        return $out;
    }

    private function timers(array $req, array &$out, bool $unassigned, ?array $active, int $now): void
    {
        $epoch = $out['progress_at'];
        $age   = $now - $epoch;
        $at    = '@' . $epoch;
        $info  = ['phase' => $out['phase'], 'responsible' => $out['responsible'], 'stalled_seconds' => $age];
        if ($out['phase'] === 'blocked') {
            $out['events'][] = ['blocked', 'blocked@' . substr(sha1($out['blocked_on']), 0, 12),
                                ['blocked_on' => $out['blocked_on'], 'owner' => (string) $req['blocked_owner']]];
        }
        if ($unassigned && $out['phase'] === 'queued' && $age >= $this->threshold('accept_seconds')) {
            $out['events'][] = ['not_assigned', 'not_assigned' . $at, $info];
            $out['timer'] = 'not_assigned';
        }
        if ($age >= $this->threshold('progress_seconds')) {
            $out['events'][] = ['no_progress', 'no_progress' . $at, $info];
            $out['timer'] = 'no_progress';
        }
        if ($age >= $this->threshold('warn_seconds')) {
            $out['events'][] = ['warning', 'warning' . $at, $info];
            $out['timer'] = 'warning';
        }
        $escalated = $age >= $this->threshold('escalate_seconds');
        if ($escalated) {
            $out['events'][] = ['timed_out', 'timed_out' . $at, $info];
            $out['events'][] = ['escalated', 'escalated' . $at, $info + ['level' => 'escalated']];
            $out['timer'] = 'escalated';
        }
        $title   = '"' . self::clip((string) $req['title'], 80) . '"';
        $pending = $out['pending'] === [] ? '' : ' Still missing: ' . implode('; ', array_map(function ($p) {
            return self::clip($p, 420);
        }, $out['pending'])) . '.';
        $details = ['request_id' => $req['id'], 'phase' => $out['phase'], 'responsible' => $out['responsible'],
                    'stalled_seconds' => $age, 'pending' => $out['pending'], 'client' => $req['client_label']];
        if ($out['phase'] === 'blocked') {
            $out['candidates']['request_blocked:' . $req['id']] = self::candidate('request_blocked', $escalated ? 2 : 1,
                "Request {$req['id']} $title is blocked: " . self::clip($out['blocked_on'], 300)
                . (trim((string) $req['blocked_owner']) !== '' ? ' (waiting on ' . $req['blocked_owner'] . ')' : '')
                . '; no progress for ' . QueueWatchPolicy::span($age) . '.' . $pending,
                'Tell EG exactly what is needed to clear it. When it is cleared, record that with request_update and do '
                . 'the next step once; close it with request_close (failed or cancelled, with the reason) if EG no longer wants it. '
                . 'Never print or resend anything to clear this alert.', $epoch, $details);
        } elseif ($age >= $this->threshold('warn_seconds')) {
            $next = $out['phase'] === 'queued'
                ? 'Nothing has been submitted for it. Do the next step, or record what it waits for with request_update '
                  . '(blocked_on), or close it with request_close if EG no longer wants it.'
                : ($active !== null ? 'Its task ' . $active['id'] . ' is ' . $active['state'] . ': see the task alerts for '
                                      . 'that worker. Do not resubmit it.'
                                    : 'Its last step finished and nothing followed. Do the next step, or record the blocker '
                                      . 'with request_update, or close it with the reason.');
            $out['candidates']['request_stalled:' . $req['id']] = self::candidate('request_stalled', $escalated ? 2 : 1,
                "Request {$req['id']} $title has made no progress for " . QueueWatchPolicy::span($age)
                . ' (stage: ' . $out['phase'] . '; responsible: ' . $out['responsible'] . ').' . $pending
                . ' Status checks and polls do not count as progress.', $next, $epoch, $details);
        }
    }

    /** One deliverable's status: missing, queued, running, submitted, unconfirmed, failed or met. */
    private function deliverable(array $d, array $tasks, ?array $outbox): array
    {
        $agent = (string) ($d['agent'] ?? '');
        $op    = (string) ($d['operation'] ?? '');
        $match = strtolower(trim((string) ($d['match'] ?? '')));
        $row   = ['key' => (string) ($d['key'] ?? ''), 'label' => (string) ($d['label'] ?? ''), 'agent' => $agent,
                  'operation' => $op, 'status' => 'missing', 'task_id' => null, 'evidence' => null, 'why' => ''];
        $found = null;
        foreach ($tasks as $t) {
            if ($t['agent'] === $agent && $t['operation'] === $op && self::matches($t, $match, $outbox)
                && ($found === null || (int) $t['created_at'] >= (int) $found['created_at'])) {
                $found = $t;
            }
        }
        if ($found === null) {
            $row['why'] = 'no ' . $agent . ' ' . $op . ' task has been submitted for it';
            if ($op === 'print' && $match !== '') {
                $row['why'] .= '; ' . self::outboxWhy($match, $outbox);
            }
            return $row;
        }
        $row['task_id'] = $found['id'];
        if (in_array($found['state'], [TaskState::QUEUED, TaskState::RUNNING], true)) {
            $row['status'] = $found['state'];
            $row['why']    = 'task ' . $found['id'] . ' is ' . $found['state'];
            return $row;
        }
        if ($found['state'] !== TaskState::SUCCEEDED) {
            $row['status'] = 'failed';
            $why = trim((string) ($found['result_summary'] ?? '')) ?: trim((string) $found['last_error']) ?: $found['state'];
            $row['why'] = 'task ' . $found['id'] . ' ' . $found['state'] . ': ' . self::clip($why, 160)
                        . ($op === 'print' ? ' (nothing printed by it; never resend without asking EG)' : '');
            return $row;
        }
        if ($op !== 'print') {
            $row['status']   = 'met';
            $row['evidence'] = 'task ' . $found['id'] . ' succeeded: ' . self::clip(trim((string) ($found['result_summary'] ?? '')), 160);
            return $row;
        }
        $job   = self::json($found['result_details'] ?? null)['job'] ?? null;
        $check = self::json($found['check_details'] ?? null)['job'] ?? null;
        $state = is_array($check) && isset($check['state']) ? (string) $check['state']
               : (is_array($job) && isset($job['state']) ? (string) $job['state'] : '');
        if ($state === 'completed_per_spooler') {
            $row['status']   = 'met';
            $row['evidence'] = 'print job ' . $found['id'] . ': Windows\' spooler reports it completed'
                             . (is_array($job) && isset($job['spooler_job_id']) ? ' (spooler job ' . $job['spooler_job_id'] . ')' : '')
                             . '; paper output not verified';
        } elseif (in_array($state, ['failed', 'canceled'], true)) {
            $row['status'] = 'failed';
            $row['why']    = 'print job ' . $found['id'] . ' ended ' . $state . ' at the spooler (never resend without asking EG)';
        } elseif ($state === 'ambiguous') {
            $row['status'] = 'unconfirmed';
            $row['why']    = 'print job ' . $found['id'] . ' may or may not have printed; EG must look at the printer before anything is resent';
        } else {
            $row['status'] = 'submitted';
            $row['why']    = 'print job ' . $found['id'] . ' was handed to EG\'s machine (' . ($state ?: 'no job state yet')
                           . '); waiting for the spooler to report it completed -- check with printer_get_status refresh=true';
        }
        return $row;
    }

    private static function matches(array $task, string $match, ?array $outbox): bool
    {
        if ($match === '') {
            return true;
        }
        $req  = self::json($task['prompt'] ?? null);
        $hay  = strtolower((string) ($req['title'] ?? '') . ' ' . (string) ($req['document_id'] ?? ''));
        foreach ((array) ($outbox['documents'] ?? []) as $doc) {
            if (is_array($doc) && isset($req['document_id']) && ($doc['document_id'] ?? null) === $req['document_id']) {
                $hay .= ' ' . strtolower((string) ($doc['name'] ?? ''));
            }
        }
        return strpos($hay, $match) !== false;
    }

    private static function outboxWhy(string $match, ?array $outbox): string
    {
        if ($outbox === null) {
            return 'the print outbox has not been listed yet, so whether the document is there is unknown';
        }
        $names = [];
        foreach ((array) $outbox['documents'] as $doc) {
            if (is_array($doc) && isset($doc['name'])) {
                if (strpos(strtolower((string) $doc['name']), $match) !== false) {
                    return 'it is in the print outbox as "' . $doc['name'] . '" (' . ($doc['document_id'] ?? '?') . ') but was not printed';
                }
                $names[] = (string) $doc['name'];
            }
        }
        return 'no document matching "' . $match . '" is in the print outbox (listing of ' . gmdate('Y-m-d H:i', (int) $outbox['at'])
             . ' UTC holds ' . ($names === [] ? 'nothing' : implode(', ', array_slice($names, 0, 6))) . ')'
             . ' -- the finished PDF has to be placed there first';
    }

    /** A status-check loop: several checks, no action, and no open request to account for them. */
    public function loops(array $calls, array $openClients, int $now): array
    {
        $by = [];
        foreach ($calls as $c) {
            $by[$c['client_id']][] = $c;
        }
        $out = [];
        foreach ($by as $client => $list) {
            if (in_array($client, $openClients, true)) {
                continue;
            }
            $checks = array_values(array_filter($list, function ($c) {
                return in_array($c['tool'], (array) $this->t['check_tools'], true);
            }));
            if (count($checks) < $this->threshold('loop_checks') || count($checks) < count($list)) {
                continue;
            }
            $label = (string) $list[0]['client_label'];
            $tools = array_count_values(array_column($checks, 'tool'));
            $named = implode(', ', array_map(function ($n, $k) { return $k . ($n > 1 ? ' x' . $n : ''); }, $tools, array_keys($tools)));
            $first = min(array_map('intval', array_column($checks, 'at')));
            $out['status_loop:' . $client] = self::candidate('status_loop', 1,
                "$label has made " . count($checks) . " status checks ($named) in the last "
                . QueueWatchPolicy::span($now - $first) . ' without submitting anything or opening a request. If EG asked for '
                . 'something, it is not being done and nothing is tracking it.',
                'Open a request with request_open for what EG asked (with its deliverables), then do the next step, or record '
                . 'with request_update exactly what it is waiting for.', $first,
                ['client' => $label, 'checks' => count($checks), 'tools' => $tools]);
        }
        return $out;
    }

    public static function json($raw): array
    {
        $d = is_string($raw) ? json_decode($raw, true) : (is_array($raw) ? $raw : null);
        return is_array($d) ? $d : [];
    }

    private static function clip(string $text, int $max): string
    {
        return strlen($text) <= $max ? $text : substr($text, 0, $max - 3) . '...';
    }

    private static function candidate(string $kind, int $level, string $problem, string $action, int $since, array $details): array
    {
        return ['kind' => $kind, 'sticky' => false, 'task_id' => null, 'problem' => $problem, 'recommended_action' => $action,
                'since' => $since, 'details' => $details, 'level' => $level];
    }
}

final class SqliteRequestStore implements IRequestStore
{
    const COLUMNS = ['client_id', 'client_label', 'idempotency_key', 'title', 'detail', 'agents', 'deliverables', 'state',
                     'phase', 'blocked_on', 'blocked_owner', 'progress_note', 'created_at', 'updated_at', 'last_progress_at',
                     'closed_at', 'outcome', 'evidence'];

    /** @var PDO */ private $pdo;

    public function __construct(PDO $pdo)
    {
        $this->pdo = $pdo;
    }

    public function insert(array $row): array
    {
        $cols = array_merge(['id'], array_values(array_intersect(self::COLUMNS, array_keys($row))));
        $this->pdo->prepare('INSERT INTO requests (' . implode(', ', $cols) . ') VALUES (' . implode(', ', array_fill(0, count($cols), '?')) . ')')
                  ->execute(array_map(function ($c) use ($row) { return $row[$c]; }, $cols));
        return $this->find($row['id']);
    }

    public function find(string $id): ?array
    {
        return $this->one('SELECT * FROM requests WHERE id = ?', [$id]);
    }

    public function findByKey(string $clientId, string $key): ?array
    {
        return $this->one('SELECT * FROM requests WHERE client_id = ? AND idempotency_key = ?', [$clientId, $key]);
    }

    public function openRequests(): array
    {
        return $this->all("SELECT * FROM requests WHERE state = 'open' ORDER BY created_at, id LIMIT 200", []);
    }

    public function closedSince(int $since): array
    {
        return $this->all("SELECT * FROM requests WHERE state <> 'open' AND closed_at >= ? ORDER BY closed_at DESC LIMIT 50", [$since]);
    }

    public function listFor(?string $clientId, bool $includeClosed): array
    {
        $where = [];
        $args  = [];
        if ($clientId !== null) {
            $where[] = 'client_id = ?';
            $args[]  = $clientId;
        }
        if (!$includeClosed) {
            $where[] = "state = 'open'";
        }
        return $this->all('SELECT * FROM requests' . ($where ? ' WHERE ' . implode(' AND ', $where) : '')
                          . ' ORDER BY created_at DESC, id DESC LIMIT 50', $args);
    }

    public function newestOpenFor(string $clientId, string $agent): ?array
    {
        foreach ($this->all("SELECT * FROM requests WHERE client_id = ? AND state = 'open' ORDER BY created_at DESC, id DESC", [$clientId]) as $r) {
            $agents = RequestPolicy::json($r['agents']);
            if ($agents === [] || in_array($agent, $agents, true)) {
                return $r;
            }
        }
        return null;
    }

    public function set(string $id, array $fields): void
    {
        $fields = array_intersect_key($fields, array_flip(self::COLUMNS));
        if ($fields === []) {
            return;
        }
        $this->pdo->prepare('UPDATE requests SET ' . implode(', ', array_map(function ($c) { return "$c = ?"; }, array_keys($fields)))
                            . ' WHERE id = ?')->execute(array_merge(array_values($fields), [$id]));
    }

    public function link(string $requestId, string $taskId, int $now): bool
    {
        $stmt = $this->pdo->prepare('INSERT OR IGNORE INTO request_tasks (task_id, request_id, linked_at) VALUES (?, ?, ?)');
        $stmt->execute([$taskId, $requestId, $now]);
        return $stmt->rowCount() === 1;
    }

    public function tasks(string $requestId): array
    {
        $rows = $this->all('SELECT t.id, t.agent, t.operation, t.state, t.attempts, t.max_attempts, t.prompt, t.last_error,
                                   t.created_at, t.updated_at, r.summary AS result_summary, r.details AS result_details,
                                   r.completed_at AS completed_at,
                                   (SELECT MAX(e.created_at) FROM task_events e WHERE e.task_id = t.id) AS last_event_at
                            FROM request_tasks rt JOIN tasks t ON t.id = rt.task_id
                            LEFT JOIN task_results r ON r.task_id = t.id
                            WHERE rt.request_id = ? ORDER BY t.created_at, t.id', [$requestId]);
        $check = $this->pdo->prepare("SELECT r.details FROM tasks t JOIN task_results r ON r.task_id = t.id
                                      WHERE t.agent = 'printer' AND t.operation = 'print_status' AND t.prompt = ?
                                      AND t.state = ? ORDER BY r.completed_at DESC LIMIT 1");
        foreach ($rows as &$row) {
            $row['check_details'] = null;
            if ($row['operation'] === 'print' && $row['state'] === TaskState::SUCCEEDED) {
                $check->execute(['job=' . $row['id'], TaskState::SUCCEEDED]);
                $found = $check->fetchColumn();
                $row['check_details'] = $found === false ? null : $found;
            }
        }
        return $rows;
    }

    public function event(string $requestId, string $event, ?string $dedupe, array $detail, int $now): bool
    {
        $stmt = $this->pdo->prepare('INSERT OR IGNORE INTO request_events (request_id, at, event, dedupe, detail) VALUES (?, ?, ?, ?, ?)');
        $stmt->execute([$requestId, $now, $event, $dedupe, json_encode($detail, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE)]);
        return $stmt->rowCount() === 1;
    }

    public function events(string $requestId, int $limit = 50): array
    {
        return array_reverse($this->all('SELECT seq, at, event, detail FROM request_events WHERE request_id = ? ORDER BY seq DESC LIMIT '
                                        . max(1, $limit), [$requestId]));
    }

    public function outbox(): ?array
    {
        $row = $this->one("SELECT r.details, r.completed_at FROM tasks t JOIN task_results r ON r.task_id = t.id
                           WHERE t.agent = 'printer' AND t.operation = 'print_discover' AND t.state = ?
                           ORDER BY r.completed_at DESC LIMIT 1", [TaskState::SUCCEEDED]);
        if ($row === null) {
            return null;
        }
        $d = RequestPolicy::json($row['details']);
        return ['at' => (int) $row['completed_at'], 'documents' => isset($d['documents']) && is_array($d['documents']) ? $d['documents'] : []];
    }

    public function recordCall(string $clientId, string $label, string $tool, int $now): void
    {
        $this->pdo->prepare('INSERT INTO client_calls (at, client_id, client_label, tool) VALUES (?, ?, ?, ?)')
                  ->execute([$now, $clientId, $label, $tool]);
        if ($now % 50 === 0) {
            $this->pdo->prepare('DELETE FROM client_calls WHERE at < ?')->execute([$now - 86400]);
        }
    }

    public function callsSince(int $since): array
    {
        return $this->all('SELECT at, client_id, client_label, tool FROM client_calls WHERE at >= ? ORDER BY at', [$since]);
    }

    private function one(string $sql, array $args): ?array
    {
        $stmt = $this->pdo->prepare($sql);
        $stmt->execute($args);
        $row = $stmt->fetch(PDO::FETCH_ASSOC);
        return $row ?: null;
    }

    private function all(string $sql, array $args): array
    {
        $stmt = $this->pdo->prepare($sql);
        $stmt->execute($args);
        return $stmt->fetchAll(PDO::FETCH_ASSOC);
    }
}

/** Opens, updates, closes and reports requests; evaluates them for AlertWatch's refresh. */
final class RequestWatch
{
    const ID = '/^rq_[0-9a-f]{24}$/';
    const OUTCOMES = ['completed', 'failed', 'cancelled'];

    /** @var IRequestStore */ private $store;
    /** @var RequestPolicy */ private $policy;
    /** @var IClock */ private $clock;
    /** @var IIdGenerator */ private $ids;
    /** @var IAuditLogger */ private $audit;
    /** @var PDO */ private $pdo;

    public function __construct(IRequestStore $store, RequestPolicy $policy, IClock $clock, IIdGenerator $ids, IAuditLogger $audit, PDO $pdo)
    {
        $this->store  = $store;
        $this->policy = $policy;
        $this->clock  = $clock;
        $this->ids    = $ids;
        $this->audit  = $audit;
        $this->pdo    = $pdo;
    }

    public function store(): IRequestStore
    {
        return $this->store;
    }

    public function policy(): RequestPolicy
    {
        return $this->policy;
    }

    public function open(Principal $who, array $args): array
    {
        $title = self::text($args, 'title', 200);
        if ($title === '') {
            throw new BridgeError(422, 'invalid_request', 'a request needs a title: what EG asked for, in a line');
        }
        $key = isset($args['idempotency_key']) ? self::text($args, 'idempotency_key', 100) : '';
        if ($key !== '' && ($existing = $this->store->findByKey($who->id, $key)) !== null) {
            return $this->view($existing, true) + ['replayed' => true];
        }
        $agents = array_values(array_unique(array_filter(array_map('strval', (array) ($args['agents'] ?? [])))));
        $deliverables = [];
        foreach ((array) ($args['deliverables'] ?? []) as $i => $d) {
            if (!is_array($d) || !isset($d['label'], $d['agent'], $d['operation'])) {
                throw new BridgeError(422, 'invalid_request', 'each deliverable needs label, agent and operation');
            }
            $deliverables[] = ['key' => 'd' . ($i + 1), 'label' => self::text($d, 'label', 120), 'agent' => self::text($d, 'agent', 40),
                               'operation' => self::text($d, 'operation', 40), 'match' => self::text($d, 'match', 120)];
            if (!in_array($deliverables[$i]['agent'], $agents, true) && $agents !== []) {
                $agents[] = $deliverables[$i]['agent'];
            }
        }
        if (count($deliverables) > 10) {
            throw new BridgeError(422, 'invalid_request', 'at most 10 deliverables');
        }
        $now = $this->clock->now();
        $id  = $this->ids->newId('rq_');
        $row = $this->store->insert(['id' => $id, 'client_id' => $who->id, 'client_label' => $who->label,
                                     'idempotency_key' => $key === '' ? null : $key, 'title' => $title,
                                     'detail' => self::text($args, 'detail', 2000),
                                     'agents' => json_encode($agents), 'deliverables' => json_encode($deliverables),
                                     'state' => 'open', 'phase' => 'queued',
                                     'blocked_on' => self::text($args, 'blocked_on', 600) ?: null,
                                     'blocked_owner' => self::text($args, 'blocked_owner', 80) ?: null,
                                     'progress_note' => null, 'created_at' => $now, 'updated_at' => $now,
                                     'last_progress_at' => $now, 'evidence' => '[]']);
        $this->store->event($id, 'opened', null, ['by' => $who->label, 'deliverables' => count($deliverables)], $now);
        if ($row['blocked_on'] !== null) {
            $this->store->event($id, 'blocked', 'blocked@' . substr(sha1((string) $row['blocked_on']), 0, 12),
                                ['blocked_on' => $row['blocked_on'], 'owner' => $row['blocked_owner']], $now);
        }
        $this->audit->record($who->id, 'request.opened', null, 'ok', ['request' => $id]);
        $this->assessAndSave($row, $now);
        return $this->view($this->store->find($id), true) + ['replayed' => false];
    }

    public function update(Principal $who, array $args): array
    {
        $row = $this->own($who, $args);
        if ($row['state'] !== 'open') {
            throw new BridgeError(409, 'closed', 'that request is already ' . $row['state']);
        }
        $now    = $this->clock->now();
        $fields = ['updated_at' => $now];
        $note   = self::text($args, 'progress', 600);
        if ($note !== '' && $note !== (string) $row['progress_note']) {
            // a new note is the client's report of real progress; repeating the same note is a poll, not progress
            $fields['progress_note']    = $note;
            $fields['last_progress_at'] = $now;
            $this->store->event($row['id'], 'progress', null, ['note' => $note, 'by' => $who->label], $now);
        }
        if (!empty($args['clear_blocker'])) {
            $fields['blocked_on'] = null;
            $fields['blocked_owner'] = null;
            $fields['last_progress_at'] = $now;
            $this->store->event($row['id'], 'unblocked', null, ['was' => $row['blocked_on'], 'by' => $who->label], $now);
        } elseif (($blocked = self::text($args, 'blocked_on', 600)) !== '') {
            $fields['blocked_on'] = $blocked;
            $fields['blocked_owner'] = self::text($args, 'blocked_owner', 80) ?: $row['blocked_owner'];
            $this->store->event($row['id'], 'blocked', 'blocked@' . substr(sha1($blocked), 0, 12),
                                ['blocked_on' => $blocked, 'owner' => $fields['blocked_owner']], $now);
        }
        $this->store->set($row['id'], $fields);
        $this->audit->record($who->id, 'request.updated', null, 'ok', ['request' => $row['id']]);
        $this->assessAndSave($this->store->find($row['id']), $now);
        return $this->view($this->store->find($row['id']), true);
    }

    public function close(Principal $who, array $args): array
    {
        $row     = $this->own($who, $args);
        $outcome = self::text($args, 'outcome', 20);
        $reason  = self::text($args, 'reason', 600);
        if (!in_array($outcome, self::OUTCOMES, true)) {
            throw new BridgeError(422, 'invalid_request', 'outcome is completed, failed or cancelled');
        }
        if ($row['state'] !== 'open') {
            if ($row['state'] === $outcome) {
                return $this->view($row, true);
            }
            throw new BridgeError(409, 'closed', 'that request is already ' . $row['state']);
        }
        if ($reason === '') {
            throw new BridgeError(422, 'invalid_request', 'say why it is closing (what EG decided, or what the evidence is)');
        }
        $now = $this->clock->now();
        $a   = $this->policy->assess($row, $this->store->tasks($row['id']), $this->store->outbox(), $now);
        $evidence = $a['evidence'];
        if ($outcome === 'completed') {
            $tasks = array_values(array_filter($this->store->tasks($row['id']), function ($t) {
                return !$this->policy->isCheck((string) $t['operation']);
            }));
            $done  = array_filter($tasks, function ($t) { return $t['state'] === TaskState::SUCCEEDED; });
            if ($a['deliverables'] !== [] && !$a['complete']) {
                throw new BridgeError(409, 'not_complete', 'not every deliverable has evidence: ' . implode('; ', $a['pending'])
                                                          . '. A request is never completed because something was submitted.');
            }
            if ($a['deliverables'] === [] && ($tasks === [] || count($done) !== count($tasks))) {
                throw new BridgeError(409, 'not_complete', 'completed needs every linked task succeeded (it has ' . count($done)
                                                          . ' of ' . count($tasks) . '); close it as failed or cancelled instead');
            }
            $evidence[] = 'closed by ' . $who->label . ': ' . $reason;
        }
        $this->finish($row, $outcome, $reason, $evidence, $who->label, $now);
        $this->audit->record($who->id, 'request.closed', null, 'ok', ['request' => $row['id'], 'outcome' => $outcome]);
        return $this->view($this->store->find($row['id']), true);
    }

    public function status(Principal $who, array $args, bool $all): array
    {
        $row = $this->own($who, $args, $all);
        return $this->view($row, true);
    }

    public function listing(Principal $who, bool $includeClosed, bool $all): array
    {
        $rows = $this->store->listFor($all ? null : $who->id, $includeClosed);
        return ['now' => $this->clock->now(), 'requests' => array_map(function ($r) { return $this->view($r, false); }, $rows)];
    }

    /** Links a newly queued task to its client's newest open request that covers its agent. */
    public function linkTask(Principal $who, array $task): ?string
    {
        $row = $this->store->newestOpenFor($who->id, (string) $task['agent']);
        if ($row === null) {
            return null;
        }
        $now = $this->clock->now();
        if ($this->store->link($row['id'], (string) $task['id'], $now)) {
            $this->store->event($row['id'], 'linked', 'linked@' . $task['id'], ['task_id' => $task['id'], 'agent' => $task['agent'],
                                'operation' => $task['operation']], $now);
            $this->store->set($row['id'], ['updated_at' => $now]);
        }
        return $row['id'];
    }

    /** The open request (if any) a new task of this client would join, and its linked tasks. */
    public function openFor(Principal $who, string $agent): ?array
    {
        $row = $this->store->newestOpenFor($who->id, $agent);
        return $row === null ? null : ['request' => $row, 'tasks' => $this->store->tasks($row['id']), 'outbox' => $this->store->outbox()];
    }

    public function recordCall(Principal $who, string $tool): void
    {
        if ($who->role === 'client' && $tool !== '') {
            $this->store->recordCall($who->id, $who->label, $tool, $this->clock->now());
        }
    }

    /** Assesses every open request, records its events, closes completed ones; returns alert candidates. */
    public function evaluate(int $now): array
    {
        $candidates = [];
        $clients    = [];
        foreach ($this->store->openRequests() as $row) {
            $clients[] = $row['client_id'];
            $a = $this->assessAndSave($row, $now);
            if ($a !== null) {
                $candidates += $a['candidates'];
            }
        }
        $window = $now - $this->policy->threshold('loop_window_seconds');
        return $candidates + $this->policy->loops($this->store->callsSince($window), array_values(array_unique($clients)), $now);
    }

    /** @return array[] open requests and those closed in the last day, as the dashboard shows them */
    public function snapshot(int $now): array
    {
        return ['open' => array_map(function ($r) { return $this->view($r, false); }, $this->store->openRequests()),
                'recently_closed' => array_map(function ($r) { return $this->view($r, false); }, $this->store->closedSince($now - 86400)),
                'thresholds' => ['accept_seconds' => $this->policy->threshold('accept_seconds'),
                                 'progress_seconds' => $this->policy->threshold('progress_seconds'),
                                 'warn_seconds' => $this->policy->threshold('warn_seconds'),
                                 'escalate_seconds' => $this->policy->threshold('escalate_seconds')]];
    }

    /** Assesses one request, persists phase, progress and events; closes it when every deliverable has evidence. */
    private function assessAndSave(array $row, int $now): ?array
    {
        $a = $this->policy->assess($row, $this->store->tasks($row['id']), $this->store->outbox(), $now);
        if ($row['state'] !== 'open') {
            return $a;
        }
        foreach ($a['events'] as list($event, $dedupe, $detail)) {
            if ($this->store->event($row['id'], $event, $dedupe, $detail, $now) && in_array($event, ['warning', 'escalated'], true)) {
                $this->audit->record('gateway-watch', 'request.' . $event, null, 'ok', ['request' => $row['id'], 'phase' => $a['phase']]);
            }
        }
        $fields = [];
        if ($a['phase'] !== $row['phase']) {
            $fields['phase'] = $a['phase'];
            $this->store->event($row['id'], 'phase', null, ['from' => $row['phase'], 'to' => $a['phase']], $now);
        }
        if ($a['progress_at'] > (int) $row['last_progress_at']) {
            $fields['last_progress_at'] = $a['progress_at'];
        }
        if ($fields !== []) {
            $this->store->set($row['id'], $fields + ['updated_at' => $now]);
        }
        if ($a['complete']) {
            $this->finish($row, 'completed', 'every deliverable has evidence', $a['evidence'], 'gateway-watch', $now);
            $this->audit->record('gateway-watch', 'request.closed', null, 'ok', ['request' => $row['id'], 'outcome' => 'completed']);
        }
        return $a;
    }

    private function finish(array $row, string $outcome, string $reason, array $evidence, string $by, int $now): void
    {
        $this->store->set($row['id'], ['state' => $outcome, 'phase' => $outcome, 'closed_at' => $now, 'updated_at' => $now,
                                       'outcome' => $reason, 'evidence' => json_encode(array_values($evidence), JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE)]);
        $this->store->event($row['id'], $outcome, $outcome, ['by' => $by, 'reason' => $reason, 'evidence' => $evidence], $now);
    }

    private function own(Principal $who, array $args, bool $all = false): array
    {
        $id = self::text($args, 'request_id', 40);
        if (!preg_match(self::ID, $id)) {
            throw new BridgeError(400, 'bad_request_id', 'request ids look like rq_ and 24 hex digits');
        }
        $row = $this->store->find($id);
        if ($row === null || (!$all && $row['client_id'] !== $who->id)) {
            throw new BridgeError(404, 'not_found', 'no such request');
        }
        return $row;
    }

    public function view(array $row, bool $full): array
    {
        $now   = $this->clock->now();
        $tasks = $this->store->tasks($row['id']);
        $a     = $this->policy->assess($row, $tasks, $this->store->outbox(), $now);
        $events = $this->store->events($row['id'], $full ? 100 : 30);
        $esc    = [];
        $recoveries = 0;
        foreach ($events as $e) {
            if (in_array($e['event'], ['not_assigned', 'no_progress', 'warning', 'timed_out', 'escalated'], true)) {
                $esc[] = ['at' => (int) $e['at'], 'event' => $e['event']];
            }
            if ($e['event'] === 'recovery') {
                $recoveries++;
            }
        }
        $open = $row['state'] === 'open';
        $out  = ['request_id' => $row['id'], 'title' => $row['title'], 'client' => $row['client_label'], 'state' => $row['state'],
                 'phase' => $open ? $a['phase'] : $row['state'], 'created_at' => (int) $row['created_at'],
                 'updated_at' => (int) $row['updated_at'], 'last_progress_at' => $a['progress_at'],
                 'since_progress_seconds' => $open ? $now - $a['progress_at'] : null,
                 'since_progress' => $open ? QueueWatchPolicy::span($now - $a['progress_at']) : null,
                 'deadline_at' => $open ? $a['deadline_at'] : null, 'timer' => $a['timer'],
                 'responsible' => $open ? $a['responsible'] : null, 'blocked_on' => $open ? ($a['blocked_on'] ?: null) : null,
                 'blocked_owner' => $row['blocked_owner'], 'progress_note' => $row['progress_note'],
                 'pending' => $open ? $a['pending'] : [], 'deliverables' => $a['deliverables'],
                 'child_tasks' => array_map(function ($t) {
                     return ['task_id' => $t['id'], 'agent' => $t['agent'], 'operation' => $t['operation'], 'state' => $t['state'],
                             'attempts' => (int) $t['attempts'], 'status_check' => $this->policy->isCheck((string) $t['operation'])];
                 }, $tasks),
                 'escalations' => $esc, 'recovery_attempts' => $recoveries,
                 'closed_at' => $row['closed_at'] === null ? null : (int) $row['closed_at'], 'outcome' => $row['outcome'],
                 'completion_evidence' => RequestPolicy::json($row['evidence'])];
        if ($full) {
            $out['detail']  = $row['detail'];
            $out['history'] = array_map(function ($e) {
                return ['at' => (int) $e['at'], 'event' => $e['event'], 'detail' => RequestPolicy::json($e['detail']) ?: new \stdClass()];
            }, $events);
        }
        return $out;
    }

    private static function text(array $args, string $name, int $max): string
    {
        $v = $args[$name] ?? '';
        return is_string($v) ? mb_substr(trim($v), 0, $max) : '';
    }
}

/**
 * Before QueueTask: a print of a document (or a text print of a title) that the client's open
 * request already printed, or is printing, is refused -- one paper copy per deliverable. A replay
 * of the same idempotency key is let through, and a failed or cancelled print does not block
 * another (resending one is still EG's decision, as the printer tools say).
 */
final class GuardRequestDuplicatesPhase implements ISubmitPhase
{
    /** @var RequestWatch */ private $requests;
    /** @var ITaskRepository */ private $tasks;

    public function __construct(RequestWatch $requests, ITaskRepository $tasks)
    {
        $this->requests = $requests;
        $this->tasks    = $tasks;
    }

    public function name(): string { return 'GuardRequestDuplicates'; }

    public function run(SubmitContext $context): void
    {
        $body = $context->body;
        if (($body['operation'] ?? '') !== 'print' || $context->principal === null) {
            return;
        }
        $key = isset($body['idempotency_key']) ? (string) $body['idempotency_key'] : null;
        if ($key !== null && $this->tasks->findByIdempotency($context->principal->id, $key) !== null) {
            return;     // a replay: QueueTask returns the original job
        }
        $open = $this->requests->openFor($context->principal, (string) $body['agent']);
        if ($open === null) {
            return;
        }
        $new = RequestPolicy::json($body['prompt']);
        foreach ($open['tasks'] as $t) {
            if ($t['operation'] !== 'print' || in_array($t['state'], [TaskState::FAILED, TaskState::CANCELLED], true)) {
                continue;
            }
            $old  = RequestPolicy::json($t['prompt']);
            $same = isset($new['document_id']) ? ($old['document_id'] ?? null) === $new['document_id']
                  : (isset($new['title']) && ($old['title'] ?? null) === $new['title'] && ($old['kind'] ?? null) === ($new['kind'] ?? null));
            if ($same) {
                throw new BridgeError(409, 'duplicate_print', 'request ' . $open['request']['id'] . ' already has print job ' . $t['id']
                    . ' for that document (task ' . $t['state'] . '). It is never sent twice: follow it with printer_get_status.');
            }
        }
    }
}

/** After QueueTask: the new task joins its client's open request, so the request sees its child tasks. */
final class LinkRequestPhase implements ISubmitPhase
{
    /** @var RequestWatch */ private $requests;

    public function __construct(RequestWatch $requests)
    {
        $this->requests = $requests;
    }

    public function name(): string { return 'LinkRequest'; }

    public function run(SubmitContext $context): void
    {
        if ($context->task === null || $context->principal === null) {
            return;
        }
        try {
            $this->requests->linkTask($context->principal, $context->task);
        } catch (\Throwable $e) {
            error_log('agent-bridge request link: ' . get_class($e) . ': ' . $e->getMessage());    // never fails a queued task
        }
    }
}

/** request_open, request_update, request_status, request_list, request_close. */
final class RequestTools implements IToolFamily
{
    const TOOLS = ['request_open', 'request_update', 'request_status', 'request_list', 'request_close'];

    /** @var RequestWatch */ private $requests;
    /** @var AlertViewers */ private $viewers;

    public function __construct(RequestWatch $requests, AlertViewers $viewers)
    {
        $this->requests = $requests;
        $this->viewers  = $viewers;
    }

    public function handles(string $name): bool
    {
        return in_array($name, self::TOOLS, true);
    }

    /** EG's identities, the same as the alert tools: a request is supervised where its alerts can be read. */
    public function offered(Principal $who): bool
    {
        return $this->viewers->allows($who);
    }

    public function instructions(): string
    {
        return 'When EG asks for something that takes more than one tool call or depends on his machine (a print, a scan, '
             . 'a download, a document to prepare), open a request first with request_open, naming its deliverables (e.g. '
             . '{label, agent: "printer", operation: "print", match: "deed"}). The gateway then watches the request itself, '
             . 'not just its tasks: tasks you submit join it, status checks do not count as progress, and a request with no '
             . 'progress for 5 minutes raises an alert, escalated at 10. If something is missing (a document not finished, '
             . 'a decision EG must make) record it with request_update blocked_on. A request completes only when every '
             . 'deliverable has evidence (a print: Windows\' spooler reports it completed -- paper is never verified); never '
             . 'call it done because a task was submitted. Close it as failed or cancelled with the reason when EG drops it.';
    }

    public function definitions(Principal $who): array
    {
        if (!$this->offered($who)) {
            return [];
        }
        $id = ['type' => 'string', 'description' => 'rq_ and 24 hex digits'];
        return [
            ['name' => 'request_open', 'title' => 'Open a supervised request',
             'description' => 'Records what EG asked for, so the gateway supervises it end to end until it is completed with '
                            . 'evidence, failed or cancelled. Tasks you submit afterwards join it. Same idempotency_key returns '
                            . 'the same request. Changes nothing on EG\'s machine.',
             'inputSchema' => ['type' => 'object', 'additionalProperties' => false, 'required' => ['title'],
                               'properties' => ['title' => ['type' => 'string', 'maxLength' => 200],
                                                'detail' => ['type' => 'string', 'maxLength' => 2000],
                                                'agents' => ['type' => 'array', 'items' => ['type' => 'string']],
                                                'deliverables' => ['type' => 'array', 'maxItems' => 10, 'items' => [
                                                    'type' => 'object', 'required' => ['label', 'agent', 'operation'],
                                                    'properties' => ['label' => ['type' => 'string'], 'agent' => ['type' => 'string'],
                                                                     'operation' => ['type' => 'string'],
                                                                     'match' => ['type' => 'string', 'description' =>
                                                                         'Words in the print title or outbox file name that identify it']]]],
                                                'blocked_on' => ['type' => 'string', 'maxLength' => 600],
                                                'blocked_owner' => ['type' => 'string', 'maxLength' => 80],
                                                'idempotency_key' => ['type' => 'string', 'maxLength' => 100]]],
             'annotations' => ['readOnlyHint' => false, 'destructiveHint' => false, 'idempotentHint' => true, 'openWorldHint' => false]],
            ['name' => 'request_update', 'title' => 'Report progress or a blocker on a request',
             'description' => 'A new progress note counts as progress (repeating one does not). blocked_on names the precise '
                            . 'prerequisite it waits for and who must clear it; clear_blocker=true when it is cleared.',
             'inputSchema' => ['type' => 'object', 'additionalProperties' => false, 'required' => ['request_id'],
                               'properties' => ['request_id' => $id, 'progress' => ['type' => 'string', 'maxLength' => 600],
                                                'blocked_on' => ['type' => 'string', 'maxLength' => 600],
                                                'blocked_owner' => ['type' => 'string', 'maxLength' => 80],
                                                'clear_blocker' => ['type' => 'boolean']]],
             'annotations' => ['readOnlyHint' => false, 'destructiveHint' => false, 'openWorldHint' => false]],
            ['name' => 'request_status', 'title' => 'Show a request',
             'description' => 'Phase, responsible worker, time since progress, pending prerequisites, deliverables with evidence, '
                            . 'child tasks, escalations and history. Reads only; does not count as progress.',
             'inputSchema' => ['type' => 'object', 'additionalProperties' => false, 'required' => ['request_id'],
                               'properties' => ['request_id' => $id]],
             'annotations' => ['readOnlyHint' => true, 'destructiveHint' => false, 'openWorldHint' => false]],
            ['name' => 'request_list', 'title' => 'List requests',
             'description' => 'Your open requests (include_closed for the last ones closed). Reads only.',
             'inputSchema' => ['type' => 'object', 'additionalProperties' => false,
                               'properties' => ['include_closed' => ['type' => 'boolean']]],
             'annotations' => ['readOnlyHint' => true, 'destructiveHint' => false, 'openWorldHint' => false]],
            ['name' => 'request_close', 'title' => 'Close a request',
             'description' => 'outcome failed or cancelled, with the reason (what EG decided). completed is refused unless every '
                            . 'deliverable already has evidence; requests with deliverables complete by themselves.',
             'inputSchema' => ['type' => 'object', 'additionalProperties' => false, 'required' => ['request_id', 'outcome', 'reason'],
                               'properties' => ['request_id' => $id,
                                                'outcome' => ['type' => 'string', 'enum' => RequestWatch::OUTCOMES],
                                                'reason' => ['type' => 'string', 'maxLength' => 600]]],
             'annotations' => ['readOnlyHint' => false, 'destructiveHint' => false, 'idempotentHint' => true, 'openWorldHint' => false]],
        ];
    }

    public function call(Request $r, Principal $who, string $name, array $args): array
    {
        if (!$this->offered($who)) {
            return ['content' => [['type' => 'text', 'text' => 'not_allowed: this client may not open supervised requests']], 'isError' => true];
        }
        try {
            $all = $this->viewers->allows($who);
            switch ($name) {
                case 'request_open':   $out = $this->requests->open($who, $args); break;
                case 'request_update': $out = $this->requests->update($who, $args); break;
                case 'request_status': $out = $this->requests->status($who, $args, $all); break;
                case 'request_list':   $out = $this->requests->listing($who, !empty($args['include_closed']), $all); break;
                default:               $out = $this->requests->close($who, $args);
            }
        } catch (BridgeError $e) {
            return ['content' => [['type' => 'text', 'text' => $e->errorCode . ': ' . $e->getMessage()]], 'isError' => true];
        }
        return ['content' => [['type' => 'text', 'text' => json_encode($out, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE)]],
                'structuredContent' => $out, 'isError' => false];
    }
}
