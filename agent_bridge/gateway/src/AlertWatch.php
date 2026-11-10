<?php
declare(strict_types=1);

namespace AgentBridge;

use PDO;

/*
 * The gateway's queue watch: the one watcher that is not on EG's machine.
 *
 * On 2026-11-10 a deed print sat "queued" for 93 minutes because EG's WSL had
 * shut down overnight, and every watcher there (the worker, the ADW Supervisor,
 * factory-ops-watch) shut down with it. The gateway kept accepting tasks and
 * said nothing. This watch looks at the gateway's own tables, so a dead
 * machine is exactly what it can see:
 *
 *   QueueWatchPolicy   pure: tables in, alert candidates out
 *   AlertEscalation    pure: how long unanswered -> which level
 *   SqliteWatchSource  reads what the policy needs (IWatchSource)
 *   SqliteWorkerContacts  when each worker last asked for work (IWorkerContacts)
 *   SqliteAlertStore   one open alert per condition, escalation, ack (IAlertStore)
 *   AlertWatch         refreshes at most every refresh_seconds, on any request
 *   AlertViewers       who may see and acknowledge alerts
 *   AlertTools         bridge_alerts / bridge_alert_acknowledge (IToolFamily)
 *   AlertNotice        adds the open alerts to every MCP tool result EG's
 *                      identities receive -- MCP cannot push, so a reply is
 *                      the only way to reach ChatGPT
 *
 * Nothing here retries, reprints or cancels anything: an alert says what is
 * wrong and what to do, and a person decides.
 */

interface IWorkerContacts
{
    public function touch(string $label, string $laneSkip, int $now): void;
    /** @return array[] worker_key, label, lane_skip, first_seen, last_seen */
    public function all(): array;
}

interface IWatchSource
{
    /** @return array[] queued or running tasks */
    public function active(): array;
    /** @return array[] failed tasks updated since $since, with their result summary */
    public function failedSince(int $since): array;
    /** @return array[] print tasks finished since $since, with result details and the latest job=<id> check */
    public function printsSince(int $since): array;
}

interface IAlertStore
{
    /**
     * Raises new candidates, refreshes open ones, clears non-sticky ones whose condition
     * is gone and escalates unacknowledged ones. Returns the alerts that changed level.
     */
    public function sync(array $candidates, int $now, AlertEscalation $escalation): array;
    /** @return array[] unresolved alerts, newest first */
    public function open(): array;
    /** @return array[] alerts resolved since $since, newest first */
    public function resolvedSince(int $since): array;
    public function find(string $id): ?array;
    /** Sticky alerts are resolved by an acknowledgement; the rest are muted until their condition clears. */
    public function acknowledge(string $id, string $by, string $note, int $now): ?array;
    public function markShown(array $ids, string $to, int $now): void;
}

/** How long an unacknowledged alert waits before each next level. Pure. */
final class AlertEscalation
{
    const NAMES = [1 => 'notice', 2 => 'escalated', 3 => 'urgent'];
    /** @var int[] seconds after raising for level 2, level 3 */ private $steps;

    public function __construct(array $steps = [900, 3600])
    {
        $this->steps = array_values(array_map('intval', $steps));
    }

    public function level(int $raisedAt, int $now): int
    {
        $level = 1;
        foreach ($this->steps as $i => $after) {
            if ($now - $raisedAt >= $after) {
                $level = $i + 2;
            }
        }
        return min($level, count(self::NAMES));
    }

    public static function name(int $level): string
    {
        return self::NAMES[$level] ?? 'urgent';
    }
}

/** Turns what the gateway's tables say into alert candidates. Pure: no clock, no database. */
final class QueueWatchPolicy
{
    const DEVICE_AGENTS  = ['printer', 'scanner'];
    const UNCONFIRMED    = ['accepted', 'queued', 'submitting', 'submitted_to_spooler'];
    const SETTLED_CHECKS = ['completed_per_spooler', 'canceled', 'failed', 'ambiguous'];

    /** @var array<string,int> */ private $t;

    public function __construct(array $thresholds = [])
    {
        $this->t = $thresholds + self::defaults();
    }

    public static function defaults(): array
    {
        return ['worker_silent_seconds' => 1200, 'queued_device_seconds' => 300, 'queued_seconds' => 600,
                'running_seconds' => 3600, 'lease_lapsed_seconds' => 120, 'print_unconfirmed_seconds' => 900, 'failure_lookback_seconds' => 86400];
    }

    public function threshold(string $name): int
    {
        return (int) $this->t[$name];
    }

    /** @return array<string,array> candidates keyed by dedupe key */
    public function evaluate(array $active, array $failed, array $prints, array $contacts, int $now): array
    {
        $out = [];
        foreach ($contacts as $c) {
            $this->workerSilent($c, $now, $out);
        }
        foreach ($active as $task) {
            $this->stalled($task, $contacts, $now, $out);
        }
        foreach ($failed as $task) {
            $this->failed($task, $out);
        }
        foreach ($prints as $task) {
            $this->unconfirmedPrint($task, $now, $out);
        }
        return $out;
    }

    private function workerSilent(array $c, int $now, array &$out): void
    {
        $quiet = $now - (int) $c['last_seen'];
        if ($quiet < $this->threshold('worker_silent_seconds')) {
            return;
        }
        $name = self::workerName($c);
        $out['worker_silent:' . $c['worker_key']] = self::candidate('worker_silent', false, null,
            "$name has not asked the gateway for work for " . self::span($quiet)
            . '. Its machine, its WSL or the worker service is down, or it cannot reach the internet; queued tasks will not run.',
            'Check that EG\'s computer is on and WSL is running (the worker restarts itself only after WSL starts), then '
            . 'check agent-bridge-worker on that machine. Nothing is lost: queued tasks run once it polls again.',
            (int) $c['last_seen'], ['worker' => $name, 'last_contact' => (int) $c['last_seen']]);
    }

    private function stalled(array $task, array $contacts, int $now, array &$out): void
    {
        $age    = $now - (int) $task['created_at'];
        $device = in_array($task['agent'], self::DEVICE_AGENTS, true);
        $what   = self::describe($task);
        if ($task['state'] === TaskState::QUEUED) {
            $limit = $this->threshold($device ? 'queued_device_seconds' : 'queued_seconds');
            if ($age < $limit) {
                return;
            }
            $silent = self::allSilent($contacts, $now, $this->threshold('worker_silent_seconds'));
            $out['queued:' . $task['id']] = self::candidate('task_queued_too_long', false, $task['id'],
                "$what has waited " . self::span($age) . ' in the queue and no worker has picked it up'
                . ($silent ? ' -- no worker has polled recently, so the machine that runs it looks offline.' : '.'),
                ($task['agent'] === 'printer'
                    ? 'Do not resend the print: it is still queued and would run twice. Bring EG\'s machine back (WSL and '
                      . 'agent-bridge-worker), or cancel it with printer_cancel if EG no longer wants it.'
                    : 'Do not resubmit: it would run twice. Bring the worker back, or cancel the task if it is no longer wanted.'),
                (int) $task['created_at'], ['agent' => $task['agent'], 'operation' => $task['operation'],
                                            'attempts' => (int) $task['attempts'], 'workers_silent' => $silent]);
        } elseif ($task['state'] === TaskState::RUNNING && isset($task['lease_expires_at'])
                  && $now - (int) $task['lease_expires_at'] >= $this->threshold('lease_lapsed_seconds')) {
            $out['worker_lost:' . $task['id']] = self::candidate('task_worker_lost', false, $task['id'],
                "$what was being run, but its worker stopped reporting " . self::span($now - (int) $task['lease_expires_at'])
                . ' ago (the worker crashed, was restarted, or lost its connection). It is requeued or failed only when a worker polls again.',
                'Check agent-bridge-worker on EG\'s machine. Do not resubmit: the task is retried automatically when a worker returns.',
                (int) $task['lease_expires_at'], ['agent' => $task['agent'], 'operation' => $task['operation'],
                                                  'attempts' => (int) $task['attempts']]);
        } elseif ($task['state'] === TaskState::RUNNING
                  && $now - ($ran = (int) ($task['claimed_at'] ?? $task['updated_at'])) >= $this->threshold('running_seconds')) {
            $out['running:' . $task['id']] = self::candidate('task_running_too_long', false, $task['id'],
                "$what has been running for " . self::span($now - $ran) . ' (its worker is still holding the lease).',
                'Look at its recent events with get_agent_task_status; cancel it if it is stuck. Do not resubmit.',
                $ran, ['agent' => $task['agent'], 'operation' => $task['operation']]);
        }
        if ((int) $task['attempts'] > 1) {
            $out['retried:' . $task['id']] = self::candidate('task_retried', false, $task['id'],
                "$what is on attempt " . (int) $task['attempts'] . ' of ' . (int) $task['max_attempts']
                . ': the worker running it stopped reporting and its lease expired (a crash, a restart or a lost connection).',
                'Watch it; if it fails again the worker is crashing on it -- check agent-bridge-worker\'s journal.',
                (int) $task['created_at'], ['attempts' => (int) $task['attempts']]);
        }
    }

    private function failed(array $task, array &$out): void
    {
        $crashed = stripos((string) $task['last_error'], 'lease expired') !== false;
        $device  = in_array($task['agent'], self::DEVICE_AGENTS, true);
        if (!$crashed && !$device) {
            return;    // an ordinary failed task is answered by its own result; the caller polls it
        }
        $why  = trim((string) ($task['result_summary'] ?? '')) ?: trim((string) $task['last_error']) ?: 'no reason recorded';
        $what = self::describe($task);
        $out['failed:' . $task['id']] = self::candidate($crashed ? 'task_crashed' : 'device_task_failed', true, $task['id'],
            "$what failed: " . self::clip($why, 240) . ($crashed ? ' (its worker stopped reporting on every attempt)' : ''),
            $task['agent'] === 'printer'
                ? 'Nothing was printed by this job. Tell EG why; fix the cause first (e.g. name a printer_id or set a '
                  . 'Windows default printer). Never resend a print without asking EG.'
                : ($task['agent'] === 'scanner' ? 'Tell EG the scan did not complete and why; start another only if EG asks.'
                                                : 'Check agent-bridge-worker\'s journal for the crash; resubmit only if wanted.'),
            (int) $task['updated_at'], ['agent' => $task['agent'], 'operation' => $task['operation'],
                                        'attempts' => (int) $task['attempts']]);
    }

    private function unconfirmedPrint(array $task, int $now, array &$out): void
    {
        $job   = self::json($task['result_details'] ?? null)['job'] ?? null;
        $check = self::json($task['check_details'] ?? null)['job'] ?? null;
        $state = is_array($check) && isset($check['state']) ? (string) $check['state']
               : (is_array($job) && isset($job['state']) ? (string) $job['state'] : '');
        $done  = (int) ($task['completed_at'] ?? $task['updated_at']);
        if ($state === 'ambiguous') {
            $out['print_ambiguous:' . $task['id']] = self::candidate('print_ambiguous', true, $task['id'],
                self::describe($task) . ' may or may not have printed: the worker could not tell whether Windows took it.',
                'Ask EG to look at the printer and its Windows queue. Never reprint automatically -- it may already be on paper.',
                $done, ['job_state' => $state]);
        } elseif (in_array($state, self::UNCONFIRMED, true) && $now - $done >= $this->threshold('print_unconfirmed_seconds')) {
            $stage = $state === 'submitted_to_spooler' ? 'Windows\' print queue accepted it but has not reported it finished'
                                                       : "it stopped at '$state' before reaching Windows' print queue";
            $out['print_unconfirmed:' . $task['id']] = self::candidate('print_unconfirmed', false, $task['id'],
                self::describe($task) . ": $stage, " . self::span($now - $done) . ' ago. Paper output is never verified.',
                'Call printer_get_status with refresh=true to ask Windows again; if it is stuck, EG checks the printer '
                . '(paper, jam, offline). Do not resend it.',
                $done, ['job_state' => $state]);
        }
    }

    public static function workerName(array $c): string
    {
        return $c['lane_skip'] === '' ? "Worker '{$c['label']}' (takes every task)"
                                      : "Worker '{$c['label']}' (passes over {$c['lane_skip']})";
    }

    private static function allSilent(array $contacts, int $now, int $limit): bool
    {
        foreach ($contacts as $c) {
            if ($now - (int) $c['last_seen'] < $limit) {
                return false;
            }
        }
        return true;
    }

    private static function describe(array $task): string
    {
        $req   = self::json($task['prompt'] ?? null);
        $title = isset($req['title']) && is_string($req['title']) ? ' "' . self::clip($req['title'], 60) . '"' : '';
        $kind  = $task['agent'] === 'printer' && $task['operation'] === 'print' ? 'Print job' . $title
               : ucfirst($task['agent']) . ' ' . $task['operation'] . ' task';
        return $kind . ' ' . $task['id'];
    }

    private static function json($raw): array
    {
        $d = is_string($raw) ? json_decode($raw, true) : null;
        return is_array($d) ? $d : [];
    }

    public static function span(int $seconds): string
    {
        $seconds = max(0, $seconds);
        if ($seconds < 120) {
            return $seconds . ' s';
        }
        if ($seconds < 7200) {
            return intdiv($seconds, 60) . ' min';
        }
        return intdiv($seconds, 3600) . ' h ' . intdiv($seconds % 3600, 60) . ' min';
    }

    private static function clip(string $text, int $max): string
    {
        return strlen($text) <= $max ? $text : substr($text, 0, $max - 3) . '...';
    }

    private static function candidate(string $kind, bool $sticky, ?string $taskId, string $problem, string $action,
                                      int $since, array $details): array
    {
        return ['kind' => $kind, 'sticky' => $sticky, 'task_id' => $taskId, 'problem' => $problem,
                'recommended_action' => $action, 'since' => $since, 'details' => $details];
    }
}

final class SqliteWorkerContacts implements IWorkerContacts
{
    /** @var PDO */ private $pdo;

    public function __construct(PDO $pdo)
    {
        $this->pdo = $pdo;
    }

    public function touch(string $label, string $laneSkip, int $now): void
    {
        $key = $label . '|' . ($laneSkip === '' ? 'all' : $laneSkip);
        $this->pdo->prepare('INSERT INTO worker_contacts (worker_key, label, lane_skip, first_seen, last_seen) VALUES (?, ?, ?, ?, ?)
                             ON CONFLICT (worker_key) DO UPDATE SET last_seen = excluded.last_seen')
                  ->execute([$key, $label, $laneSkip, $now, $now]);
    }

    public function all(): array
    {
        return $this->pdo->query('SELECT * FROM worker_contacts ORDER BY worker_key')->fetchAll(PDO::FETCH_ASSOC);
    }
}

final class SqliteWatchSource implements IWatchSource
{
    const WITH_RESULT = 'SELECT t.*, r.ok AS result_ok, r.summary AS result_summary, r.details AS result_details,
                                r.completed_at AS completed_at FROM tasks t LEFT JOIN task_results r ON r.task_id = t.id';

    /** @var PDO */ private $pdo;

    public function __construct(PDO $pdo)
    {
        $this->pdo = $pdo;
    }

    public function active(): array
    {
        $stmt = $this->pdo->prepare('SELECT t.*, l.expires_at AS lease_expires_at, l.claimed_at AS claimed_at FROM tasks t
                                     LEFT JOIN worker_leases l ON l.task_id = t.id AND t.state = \'running\'
                                     WHERE t.state IN (?, ?) ORDER BY t.created_at LIMIT 200');
        $stmt->execute([TaskState::QUEUED, TaskState::RUNNING]);
        return $stmt->fetchAll(PDO::FETCH_ASSOC);
    }

    public function failedSince(int $since): array
    {
        $stmt = $this->pdo->prepare(self::WITH_RESULT . ' WHERE t.state = ? AND t.updated_at >= ? ORDER BY t.updated_at LIMIT 200');
        $stmt->execute([TaskState::FAILED, $since]);
        return $stmt->fetchAll(PDO::FETCH_ASSOC);
    }

    public function printsSince(int $since): array
    {
        $stmt = $this->pdo->prepare(self::WITH_RESULT . " WHERE t.agent = 'printer' AND t.operation = 'print' AND t.state = ?
                                     AND r.completed_at >= ? ORDER BY r.completed_at LIMIT 100");
        $stmt->execute([TaskState::SUCCEEDED, $since]);
        $rows  = $stmt->fetchAll(PDO::FETCH_ASSOC);
        $check = $this->pdo->prepare("SELECT r.details FROM tasks t JOIN task_results r ON r.task_id = t.id
                                      WHERE t.agent = 'printer' AND t.operation = 'print_status' AND t.prompt = ?
                                      AND t.state = ? ORDER BY r.completed_at DESC LIMIT 1");
        foreach ($rows as &$row) {
            $check->execute(['job=' . $row['id'], TaskState::SUCCEEDED]);
            $found = $check->fetchColumn();
            $row['check_details'] = $found === false ? null : $found;
        }
        return $rows;
    }
}

final class SqliteAlertStore implements IAlertStore
{
    /** @var PDO */ private $pdo;
    /** @var IIdGenerator */ private $ids;

    public function __construct(PDO $pdo, IIdGenerator $ids)
    {
        $this->pdo = $pdo;
        $this->ids = $ids;
    }

    public function sync(array $candidates, int $now, AlertEscalation $escalation): array
    {
        return Database::immediate($this->pdo, function () use ($candidates, $now, $escalation) {
            $open = [];
            foreach ($this->open() as $row) {
                $open[$row['dedupe_key']] = $row;
            }
            $changed = [];
            foreach ($candidates as $key => $c) {
                if (isset($open[$key])) {
                    $this->pdo->prepare('UPDATE alerts SET problem = ?, recommended_action = ?, last_seen = ?, details = ? WHERE id = ?')
                              ->execute([$c['problem'], $c['recommended_action'], $now, json_encode($c['details']), $open[$key]['id']]);
                    continue;
                }
                if ($c['sticky'] && $this->everRaised($key)) {
                    continue;     // a failure EG has acknowledged is not raised again
                }
                $id = $this->ids->newId('al_');
                $this->pdo->prepare('INSERT INTO alerts (id, dedupe_key, kind, sticky, task_id, problem, recommended_action, since,
                                     raised_at, last_seen, level, details) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)')
                          ->execute([$id, $key, $c['kind'], $c['sticky'] ? 1 : 0, $c['task_id'], $c['problem'],
                                     $c['recommended_action'], $c['since'], $now, $now, json_encode($c['details'])]);
                $changed[] = ['id' => $id, 'kind' => $c['kind'], 'task_id' => $c['task_id'], 'level' => 1];
            }
            foreach ($open as $key => $row) {
                if (!isset($candidates[$key]) && (int) $row['sticky'] === 0) {
                    $this->pdo->prepare('UPDATE alerts SET resolved_at = ?, resolution = ? WHERE id = ?')
                              ->execute([$now, 'the condition cleared', $row['id']]);
                    continue;
                }
                if ($row['acknowledged_at'] !== null) {
                    continue;     // acknowledged: EG knows, so it is not escalated further
                }
                $level = $escalation->level((int) $row['raised_at'], $now);
                if ($level > (int) $row['level']) {
                    $this->pdo->prepare('UPDATE alerts SET level = ?, escalated_at = ? WHERE id = ?')->execute([$level, $now, $row['id']]);
                    $changed[] = ['id' => $row['id'], 'kind' => $row['kind'], 'task_id' => $row['task_id'], 'level' => $level];
                }
            }
            return $changed;
        });
    }

    private function everRaised(string $key): bool
    {
        $stmt = $this->pdo->prepare('SELECT 1 FROM alerts WHERE dedupe_key = ? LIMIT 1');
        $stmt->execute([$key]);
        return $stmt->fetchColumn() !== false;
    }

    public function open(): array
    {
        return $this->pdo->query('SELECT * FROM alerts WHERE resolved_at IS NULL ORDER BY raised_at DESC, id DESC LIMIT 100')
                         ->fetchAll(PDO::FETCH_ASSOC);
    }

    public function resolvedSince(int $since): array
    {
        $stmt = $this->pdo->prepare('SELECT * FROM alerts WHERE resolved_at >= ? ORDER BY resolved_at DESC LIMIT 50');
        $stmt->execute([$since]);
        return $stmt->fetchAll(PDO::FETCH_ASSOC);
    }

    public function find(string $id): ?array
    {
        $stmt = $this->pdo->prepare('SELECT * FROM alerts WHERE id = ?');
        $stmt->execute([$id]);
        $row = $stmt->fetch(PDO::FETCH_ASSOC);
        return $row ?: null;
    }

    public function acknowledge(string $id, string $by, string $note, int $now): ?array
    {
        $row = $this->find($id);
        if ($row === null || $row['resolved_at'] !== null) {
            return $row;
        }
        $resolve = (int) $row['sticky'] === 1;
        $this->pdo->prepare('UPDATE alerts SET acknowledged_at = COALESCE(acknowledged_at, ?), acknowledged_by = ?, ack_note = ?,
                             resolved_at = ?, resolution = ? WHERE id = ?')
                  ->execute([$now, $by, $note, $resolve ? $now : null, $resolve ? 'acknowledged' : null, $id]);
        return $this->find($id);
    }

    public function markShown(array $ids, string $to, int $now): void
    {
        $stmt = $this->pdo->prepare('UPDATE alerts SET shown_count = shown_count + 1, last_shown_at = ?, last_shown_to = ? WHERE id = ?');
        foreach ($ids as $id) {
            $stmt->execute([$now, $to, $id]);
        }
    }
}

/** Runs the policy against the tables at most once every refresh_seconds, whatever request comes in. */
final class AlertWatch
{
    /** @var IWatchSource */ private $source;
    /** @var IWorkerContacts */ private $contacts;
    /** @var IAlertStore */ private $store;
    /** @var QueueWatchPolicy */ private $policy;
    /** @var AlertEscalation */ private $escalation;
    /** @var IClock */ private $clock;
    /** @var IAuditLogger */ private $audit;
    /** @var PDO */ private $pdo;
    /** @var int */ private $refreshSeconds;

    public function __construct(IWatchSource $source, IWorkerContacts $contacts, IAlertStore $store, QueueWatchPolicy $policy,
                                AlertEscalation $escalation, IClock $clock, IAuditLogger $audit, PDO $pdo, int $refreshSeconds = 30)
    {
        $this->source         = $source;
        $this->contacts       = $contacts;
        $this->store          = $store;
        $this->policy         = $policy;
        $this->escalation     = $escalation;
        $this->clock          = $clock;
        $this->audit          = $audit;
        $this->pdo            = $pdo;
        $this->refreshSeconds = $refreshSeconds;
    }

    public function workerSeen(Principal $worker, array $skip): void
    {
        sort($skip);
        $this->contacts->touch($worker->label, implode(',', $skip), $this->clock->now());
    }

    /** Refreshes when the last refresh is older than refresh_seconds. Never throws: a watch must not break a request. */
    public function tick(): void
    {
        try {
            $now  = $this->clock->now();
            $last = (int) $this->meta('last_refresh');
            if ($now - $last >= $this->refreshSeconds) {
                $this->refresh();
            }
        } catch (\Throwable $e) {
            error_log('agent-bridge watch: ' . get_class($e) . ': ' . $e->getMessage());
        }
    }

    /** @return array[] alerts raised or escalated by this refresh */
    public function refresh(): array
    {
        $now = $this->clock->now();
        $this->setMeta('last_refresh', (string) $now);
        $lookback   = $now - $this->policy->threshold('failure_lookback_seconds');
        $installed  = (int) ($this->meta('installed_at') ?: $now);
        if ($this->meta('installed_at') === null) {
            $this->setMeta('installed_at', (string) $now);
        }
        // Failures from before the watch existed are history, not news: look back no further than install minus a day.
        $since      = max($lookback, $installed - 86400);
        $candidates = $this->policy->evaluate($this->source->active(), $this->source->failedSince($since),
                                              $this->source->printsSince($since), $this->contacts->all(), $now);
        $changed    = $this->store->sync($candidates, $now, $this->escalation);
        foreach ($changed as $c) {
            $this->audit->record('gateway-watch', $c['level'] === 1 ? 'alert.raised' : 'alert.escalated', $c['task_id'], 'ok',
                                 ['alert' => $c['id'], 'kind' => $c['kind'], 'level' => AlertEscalation::name($c['level'])]);
        }
        return $changed;
    }

    public function lastRefresh(): int
    {
        return (int) $this->meta('last_refresh');
    }

    public function contacts(): array
    {
        $now = $this->clock->now();
        return array_map(function ($c) use ($now) {
            return ['worker' => QueueWatchPolicy::workerName($c), 'last_contact' => (int) $c['last_seen'],
                    'quiet_seconds' => $now - (int) $c['last_seen']];
        }, $this->contacts->all());
    }

    /** The snapshot every reader shows: open alerts (unacknowledged first), recent resolutions, and worker contact. */
    public function snapshot(): array
    {
        $now  = $this->clock->now();
        $open = array_map(function ($a) use ($now) { return self::view($a, $now); }, $this->store->open());
        usort($open, function ($a, $b) {
            return [$a['acknowledged'], -$a['level']] <=> [$b['acknowledged'], -$b['level']];
        });
        return ['checked_at' => $this->lastRefresh(), 'now' => $now,
                'unacknowledged' => count(array_filter($open, function ($a) { return !$a['acknowledged']; })),
                'alerts' => $open,
                'recently_resolved' => array_map(function ($a) use ($now) { return self::view($a, $now); },
                                                 $this->store->resolvedSince($now - 86400)),
                'workers' => $this->contacts()];
    }

    public function store(): IAlertStore
    {
        return $this->store;
    }

    public static function view(array $a, int $now): array
    {
        $details = json_decode((string) $a['details'], true);
        $end     = $a['resolved_at'] !== null ? (int) $a['resolved_at'] : $now;
        return ['alert_id' => $a['id'], 'kind' => $a['kind'], 'task_id' => $a['task_id'], 'problem' => $a['problem'],
                'recommended_action' => $a['recommended_action'], 'level' => (int) $a['level'],
                'severity' => AlertEscalation::name((int) $a['level']),
                'since' => (int) $a['since'], 'duration_seconds' => $end - (int) $a['since'],
                'duration' => QueueWatchPolicy::span($end - (int) $a['since']),
                'raised_at' => (int) $a['raised_at'], 'escalated_at' => $a['escalated_at'] === null ? null : (int) $a['escalated_at'],
                'acknowledged' => $a['acknowledged_at'] !== null, 'acknowledged_by' => $a['acknowledged_by'],
                'acknowledged_at' => $a['acknowledged_at'] === null ? null : (int) $a['acknowledged_at'],
                'resolved_at' => $a['resolved_at'] === null ? null : (int) $a['resolved_at'], 'resolution' => $a['resolution'],
                'stays_until' => (int) $a['sticky'] === 1 ? 'acknowledged' : 'the condition clears',
                'shown_to_client' => (int) $a['shown_count'], 'last_shown_at' => $a['last_shown_at'] === null ? null : (int) $a['last_shown_at'],
                'details' => is_array($details) ? $details : new \stdClass()];
    }

    public function acknowledge(Principal $who, string $id, string $note): array
    {
        if (!preg_match('/^al_[0-9a-f]{24}$/', $id)) {
            throw new BridgeError(400, 'bad_alert_id', 'alert ids look like al_ and 24 hex digits');
        }
        $row = $this->store->acknowledge($id, $who->label, substr(trim($note), 0, 300), $this->clock->now());
        if ($row === null) {
            throw new BridgeError(404, 'not_found', 'no such alert');
        }
        $this->audit->record($who->id, 'alert.acknowledged', $row['task_id'], 'ok', ['alert' => $id]);
        return self::view($row, $this->clock->now());
    }

    private function meta(string $name): ?string
    {
        $stmt = $this->pdo->prepare('SELECT value FROM watch_meta WHERE name = ?');
        $stmt->execute([$name]);
        $v = $stmt->fetchColumn();
        return $v === false ? null : (string) $v;
    }

    private function setMeta(string $name, string $value): void
    {
        $this->pdo->prepare('INSERT INTO watch_meta (name, value) VALUES (?, ?) ON CONFLICT (name) DO UPDATE SET value = excluded.value')
                  ->execute([$name, $value]);
    }
}

/** Who may see and acknowledge alerts: the named ids or labels, plus every identity bound in client_scopes (EG's). */
final class AlertViewers
{
    /** @var string[] */ private $named;
    /** @var string[] */ private $bound;

    public function __construct(array $named, array $clientScopeBindings)
    {
        $this->named = array_map('strval', $named);
        $this->bound = array_map('strval', array_keys($clientScopeBindings));
    }

    public function allows(Principal $who): bool
    {
        return $who->role === 'client' && (in_array($who->id, $this->named, true) || in_array($who->label, $this->named, true)
                                           || in_array($who->id, $this->bound, true) || in_array('*', $this->named, true));
    }
}

/** bridge_alerts and bridge_alert_acknowledge. */
final class AlertTools implements IToolFamily
{
    const TOOLS = ['bridge_alerts', 'bridge_alert_acknowledge'];

    /** @var AlertWatch */ private $watch;
    /** @var AlertViewers */ private $viewers;

    public function __construct(AlertWatch $watch, AlertViewers $viewers)
    {
        $this->watch   = $watch;
        $this->viewers = $viewers;
    }

    public function handles(string $name): bool
    {
        return in_array($name, self::TOOLS, true);
    }

    public function offered(Principal $who): bool
    {
        return $this->viewers->allows($who);
    }

    public function instructions(): string
    {
        return 'The gateway watches its own queue, off EG\'s machine: a task stuck in the queue, a worker that stops '
             . 'polling, a failed or unconfirmed print, or a crashed task raises an alert. Open alerts are appended to '
             . 'every tool result as a "BRIDGE ALERTS" block until acknowledged; tell EG about each one, then call '
             . 'bridge_alert_acknowledge. bridge_alerts lists them with duration and recommended action. Never reprint or '
             . 'resubmit to clear an alert.';
    }

    public function definitions(Principal $who): array
    {
        if (!$this->offered($who)) {
            return [];
        }
        return [
            ['name' => 'bridge_alerts', 'title' => 'List Bridge alerts',
             'description' => 'Open alerts from the gateway\'s queue watch (stalled, failed, crashed or unconfirmed tasks and '
                            . 'silent workers), each with job id, problem, duration, severity and recommended action; plus '
                            . 'alerts resolved in the last day and when each worker last polled. Never changes anything.',
             'inputSchema' => ['type' => 'object', 'properties' => new \stdClass(), 'additionalProperties' => false],
             'annotations' => ['readOnlyHint' => true, 'destructiveHint' => false, 'openWorldHint' => false]],
            ['name' => 'bridge_alert_acknowledge', 'title' => 'Acknowledge a Bridge alert',
             'description' => 'Records that EG has been told about an alert. A failure alert is then resolved; a condition '
                            . 'alert (a stuck queue, a silent worker) stops being repeated but stays listed until it clears.',
             'inputSchema' => ['type' => 'object', 'additionalProperties' => false, 'required' => ['alert_id'],
                               'properties' => ['alert_id' => ['type' => 'string'],
                                                'note' => ['type' => 'string', 'maxLength' => 300,
                                                           'description' => 'Optional: what EG said or decided']]],
             'annotations' => ['readOnlyHint' => false, 'destructiveHint' => false, 'idempotentHint' => true, 'openWorldHint' => false]],
        ];
    }

    public function call(Request $r, Principal $who, string $name, array $args): array
    {
        if (!$this->offered($who)) {
            return ['content' => [['type' => 'text', 'text' => 'not_allowed: this client may not read Bridge alerts']], 'isError' => true];
        }
        try {
            if ($name === 'bridge_alerts') {
                $this->watch->refresh();
                $out = $this->watch->snapshot();
                $this->watch->store()->markShown(array_column(array_filter($out['alerts'], function ($a) {
                    return !$a['acknowledged'];
                }), 'alert_id'), $who->label, $out['now']);
            } else {
                $out = $this->watch->acknowledge($who, isset($args['alert_id']) ? (string) $args['alert_id'] : '',
                                                 isset($args['note']) && is_string($args['note']) ? $args['note'] : '');
            }
        } catch (BridgeError $e) {
            return ['content' => [['type' => 'text', 'text' => $e->errorCode . ': ' . $e->getMessage()]], 'isError' => true];
        }
        return ['content' => [['type' => 'text', 'text' => json_encode($out, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE)]],
                'structuredContent' => $out, 'isError' => false];
    }
}

/** Appends the open, unacknowledged alerts to a tool result, and records that this reply carried them. */
final class AlertNotice
{
    const MAX_LISTED = 5;

    /** @var AlertWatch */ private $watch;
    /** @var AlertViewers */ private $viewers;
    /** @var IClock */ private $clock;

    public function __construct(AlertWatch $watch, AlertViewers $viewers, IClock $clock)
    {
        $this->watch   = $watch;
        $this->viewers = $viewers;
        $this->clock   = $clock;
    }

    public function annotate(Principal $who, string $tool, array $result): array
    {
        if (!$this->viewers->allows($who) || in_array($tool, AlertTools::TOOLS, true)) {
            return $result;
        }
        try {
            $now     = $this->clock->now();
            $pending = array_values(array_filter($this->watch->store()->open(), function ($a) {
                return $a['acknowledged_at'] === null;
            }));
            if ($pending === []) {
                return $result;
            }
            usort($pending, function ($a, $b) { return (int) $b['level'] <=> (int) $a['level']; });
            $lines = [];
            foreach (array_slice($pending, 0, self::MAX_LISTED) as $a) {
                $v       = AlertWatch::view($a, $now);
                $lines[] = '- [' . strtoupper($v['severity']) . '] ' . $v['alert_id'] . ($v['task_id'] ? ' (job ' . $v['task_id'] . ')' : '')
                         . ', for ' . $v['duration'] . ': ' . rtrim($v['problem'], '. ') . '. Recommended: ' . $v['recommended_action'];
            }
            $more = count($pending) > self::MAX_LISTED ? "\n(" . (count($pending) - self::MAX_LISTED) . ' more: call bridge_alerts.)' : '';
            $result['content'][] = ['type' => 'text', 'text' => 'BRIDGE ALERTS -- ' . count($pending)
                . " unacknowledged. Tell EG about these before carrying on, then call bridge_alert_acknowledge for each:\n"
                . implode("\n", $lines) . $more];
            $this->watch->store()->markShown(array_column(array_slice($pending, 0, self::MAX_LISTED), 'id'), $who->label, $now);
        } catch (\Throwable $e) {
            error_log('agent-bridge alert notice: ' . get_class($e));
        }
        return $result;
    }
}
