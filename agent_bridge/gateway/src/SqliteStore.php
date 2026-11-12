<?php
declare(strict_types=1);

namespace AgentBridge;

use PDO;

/** Opens the database with the settings every adapter here relies on. */
final class Database
{
    public static function open(string $path): PDO
    {
        $pdo = new PDO('sqlite:' . $path, null, null, [
            PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        ]);
        $pdo->exec('PRAGMA busy_timeout = 5000');
        $pdo->exec('PRAGMA foreign_keys = ON');
        if ($path !== ':memory:') {
            $pdo->exec('PRAGMA journal_mode = WAL');
        }
        return $pdo;
    }

    /** Runs $work inside BEGIN IMMEDIATE, so the write lock is taken up
     *  front and two claims cannot interleave between read and update. */
    public static function immediate(PDO $pdo, callable $work)
    {
        $pdo->exec('BEGIN IMMEDIATE');
        try {
            $value = $work();
            $pdo->exec('COMMIT');
            return $value;
        } catch (\Throwable $e) {
            $pdo->exec('ROLLBACK');
            throw $e;
        }
    }
}

final class SqliteTaskStore implements ITaskRepository, ITaskLeaseManager
{
    /** @var PDO */    private $pdo;
    /** @var IClock */ private $clock;
    /** @var IIdGenerator */ private $ids;

    public function __construct(PDO $pdo, IClock $clock, IIdGenerator $ids)
    {
        $this->pdo   = $pdo;
        $this->clock = $clock;
        $this->ids   = $ids;
    }

    // ---- ITaskRepository ------------------------------------------------

    public function insert(array $task): array
    {
        $now = $this->clock->now();
        $row = [
            'id'              => $this->ids->newId('t_'),
            'client_id'       => $task['client_id'],
            'idempotency_key' => $task['idempotency_key'] ?? null,
            'schema_version'  => $task['schema_version'],
            'task_type'       => $task['task_type'],
            'agent'           => $task['agent'],
            'workspace'       => $task['workspace'],
            'operation'       => $task['operation'],
            'requested_mode'  => $task['requested_mode'],
            'approval_mode'   => $task['approval_mode'],
            'prompt'          => $task['prompt'],
            'state'           => TaskState::QUEUED,
            'max_attempts'    => (int) $task['max_attempts'],
            'created_at'      => $now,
            'updated_at'      => $now,
        ];
        $columns = implode(', ', array_keys($row));
        $marks   = implode(', ', array_map(function ($k) { return ':' . $k; }, array_keys($row)));
        $this->pdo->prepare("INSERT INTO tasks ($columns) VALUES ($marks)")->execute($row);
        $this->appendEvent($row['id'], 'state', 'queued');
        return $this->find($row['id']);
    }

    public function findByIdempotency(string $clientId, string $key): ?array
    {
        $stmt = $this->pdo->prepare('SELECT * FROM tasks WHERE client_id = ? AND idempotency_key = ?');
        $stmt->execute([$clientId, $key]);
        $row = $stmt->fetch();
        return $row ?: null;
    }

    public function find(string $id): ?array
    {
        $stmt = $this->pdo->prepare('SELECT * FROM tasks WHERE id = ?');
        $stmt->execute([$id]);
        $row = $stmt->fetch();
        return $row ?: null;
    }

    public function events(string $id, int $afterSeq): array
    {
        // Internal status sentinel: newest bounded window, independent of task age.
        if ($afterSeq === -1) {
            $stmt = $this->pdo->prepare(
                'SELECT seq, kind, message, created_at FROM task_events WHERE task_id = ? ORDER BY seq DESC LIMIT 50');
            $stmt->execute([$id]);
            return array_reverse($stmt->fetchAll());
        }
        $stmt = $this->pdo->prepare(
            'SELECT seq, kind, message, created_at FROM task_events WHERE task_id = ? AND seq > ? ORDER BY seq LIMIT 500');
        $stmt->execute([$id, $afterSeq]);
        return $stmt->fetchAll();
    }

    public function appendEvent(string $id, string $kind, string $message): void
    {
        $this->pdo->prepare('INSERT INTO task_events (task_id, kind, message, created_at) VALUES (?, ?, ?, ?)')
                  ->execute([$id, $kind, $message, $this->clock->now()]);
    }

    public function result(string $id): ?array
    {
        $stmt = $this->pdo->prepare('SELECT * FROM task_results WHERE task_id = ?');
        $stmt->execute([$id]);
        $row = $stmt->fetch();
        return $row ?: null;
    }

    public function requestCancel(string $id): ?array
    {
        return Database::immediate($this->pdo, function () use ($id) {
            $task = $this->find($id);
            if ($task === null) {
                return null;
            }
            if ($task['state'] === TaskState::QUEUED) {
                $this->move($task, TaskState::CANCELLED, 'cancelled before a worker claimed it');
            } elseif ($task['state'] === TaskState::RUNNING) {
                $this->pdo->prepare('UPDATE tasks SET cancel_requested = 1, updated_at = ? WHERE id = ?')
                          ->execute([$this->clock->now(), $id]);
                $this->appendEvent($id, 'cancel', 'cancel requested; the worker stops at its next heartbeat');
            }
            return $this->find($id);
        });
    }

    // ---- ITaskLeaseManager ----------------------------------------------

    public function next(array $skip = [], ?array $only = null, array $exclude = []): ?array
    {
        $where = 'state = ?';
        $args  = [TaskState::QUEUED];
        if ($only !== null) {
            if (!$only) {
                return null;
            }
            $where .= ' AND workspace IN (' . implode(',', array_fill(0, count($only), '?')) . ')';
            array_push($args, ...array_values($only));
        }
        if ($exclude) {
            $where .= ' AND workspace NOT IN (' . implode(',', array_fill(0, count($exclude), '?')) . ')';
            array_push($args, ...array_values($exclude));
        }
        foreach ($skip as $pair) {
            [$operation, $workspace] = explode('@', $pair, 2);
            $where .= ' AND NOT (operation = ? AND workspace = ?)';
            array_push($args, $operation, $workspace);
        }
        $stmt = $this->pdo->prepare("SELECT * FROM tasks WHERE $where ORDER BY created_at, id LIMIT 1");
        $stmt->execute($args);
        $row = $stmt->fetch();
        return $row ?: null;
    }

    public function claim(string $id, string $workerId, int $leaseSeconds): ?array
    {
        return Database::immediate($this->pdo, function () use ($id, $workerId, $leaseSeconds) {
            $task = $this->find($id);
            if ($task === null || $task['state'] !== TaskState::QUEUED || (int) $task['cancel_requested'] === 1) {
                return null;
            }
            $now   = $this->clock->now();
            $token = $this->ids->newSecret();
            $moved = $this->pdo->prepare(
                'UPDATE tasks SET state = ?, attempts = attempts + 1, updated_at = ? WHERE id = ? AND state = ?');
            $moved->execute([TaskState::RUNNING, $now, $id, TaskState::QUEUED]);
            if ($moved->rowCount() !== 1) {
                return null;
            }
            $this->pdo->prepare('DELETE FROM worker_leases WHERE task_id = ?')->execute([$id]);
            $this->pdo->prepare(
                'INSERT INTO worker_leases (task_id, worker_id, lease_hash, expires_at, claimed_at) VALUES (?, ?, ?, ?, ?)')
                ->execute([$id, $workerId, hash('sha256', $token), $now + $leaseSeconds, $now]);
            $this->appendEvent($id, 'state', 'running (claimed by ' . $workerId . ')');
            $claimed = $this->find($id);
            $claimed['lease_token']      = $token;
            $claimed['lease_expires_at'] = $now + $leaseSeconds;
            return $claimed;
        });
    }

    public function heartbeat(string $id, string $leaseToken, int $leaseSeconds): ?array
    {
        return Database::immediate($this->pdo, function () use ($id, $leaseToken, $leaseSeconds) {
            if (!$this->leaseValid($id, $leaseToken)) {
                return null;
            }
            $expires = $this->clock->now() + $leaseSeconds;
            $this->pdo->prepare('UPDATE worker_leases SET expires_at = ? WHERE task_id = ?')->execute([$expires, $id]);
            $task = $this->find($id);
            return ['cancel_requested' => (int) $task['cancel_requested'] === 1, 'lease_expires_at' => $expires];
        });
    }

    public function complete(string $id, string $leaseToken, array $result): bool
    {
        return (bool) Database::immediate($this->pdo, function () use ($id, $leaseToken, $result) {
            if (!$this->leaseValid($id, $leaseToken)) {
                return false;
            }
            $task = $this->find($id);
            $ok   = !empty($result['ok']);
            $this->pdo->prepare(
                'INSERT OR REPLACE INTO task_results (task_id, ok, summary, body, performer, details, completed_at)
                 VALUES (?, ?, ?, ?, ?, ?, ?)')
                ->execute([$id, $ok ? 1 : 0, (string) $result['summary'], (string) $result['body'],
                           (string) $result['performer'], isset($result['details']) ? (string) $result['details'] : '{}',
                           $this->clock->now()]);
            $this->move($task, $ok ? TaskState::SUCCEEDED : TaskState::FAILED, $ok ? 'succeeded' : 'failed: agent reported failure');
            $this->pdo->prepare('DELETE FROM worker_leases WHERE task_id = ?')->execute([$id]);
            return true;
        });
    }

    public function fail(string $id, string $leaseToken, string $reason, bool $retryable, bool $cancelled): ?string
    {
        return Database::immediate($this->pdo, function () use ($id, $leaseToken, $reason, $retryable, $cancelled) {
            if (!$this->leaseValid($id, $leaseToken)) {
                return null;
            }
            $task = $this->find($id);
            $this->pdo->prepare('DELETE FROM worker_leases WHERE task_id = ?')->execute([$id]);
            if ($cancelled) {
                return $this->move($task, TaskState::CANCELLED, 'cancelled: ' . $reason, $reason);
            }
            $again = $retryable && (int) $task['attempts'] < (int) $task['max_attempts'] && (int) $task['cancel_requested'] === 0;
            return $this->move($task, $again ? TaskState::QUEUED : TaskState::FAILED,
                               ($again ? 'queued for retry: ' : 'failed: ') . $reason, $reason);
        });
    }

    public function recoverExpired(): int
    {
        return (int) Database::immediate($this->pdo, function () {
            $stmt = $this->pdo->prepare(
                'SELECT t.* FROM tasks t JOIN worker_leases l ON l.task_id = t.id WHERE t.state = ? AND l.expires_at < ?');
            $stmt->execute([TaskState::RUNNING, $this->clock->now()]);
            $count = 0;
            foreach ($stmt->fetchAll() as $task) {
                $this->pdo->prepare('DELETE FROM worker_leases WHERE task_id = ?')->execute([$task['id']]);
                if ((int) $task['cancel_requested'] === 1) {
                    $this->move($task, TaskState::CANCELLED, 'cancelled: lease expired after a cancel request');
                } elseif ((int) $task['attempts'] < (int) $task['max_attempts']) {
                    $this->move($task, TaskState::QUEUED, 'queued again: the worker lease expired', 'lease expired');
                } else {
                    $this->move($task, TaskState::FAILED, 'failed: the worker lease expired and no attempts remain', 'lease expired');
                }
                $count++;
            }
            return $count;
        });
    }

    // ---- helpers --------------------------------------------------------

    private function leaseValid(string $id, string $leaseToken): bool
    {
        $stmt = $this->pdo->prepare(
            'SELECT l.lease_hash, l.expires_at FROM worker_leases l JOIN tasks t ON t.id = l.task_id WHERE l.task_id = ? AND t.state = ?');
        $stmt->execute([$id, TaskState::RUNNING]);
        $lease = $stmt->fetch();
        return $lease !== false
            && (int) $lease['expires_at'] >= $this->clock->now()
            && hash_equals((string) $lease['lease_hash'], hash('sha256', $leaseToken));
    }

    private function move(array $task, string $to, string $message, ?string $error = null): string
    {
        if (!TaskState::canMove((string) $task['state'], $to)) {
            throw new BridgeError(409, 'invalid_transition', 'cannot move a ' . $task['state'] . ' task to ' . $to);
        }
        $stmt = $this->pdo->prepare('UPDATE tasks SET state = ?, last_error = COALESCE(?, last_error), updated_at = ? WHERE id = ? AND state = ?');
        $stmt->execute([$to, $error, $this->clock->now(), $task['id'], $task['state']]);
        if ($stmt->rowCount() !== 1) {
            throw new BridgeError(409, 'conflict', 'the task changed state underneath this request');
        }
        $this->appendEvent((string) $task['id'], 'state', $message);
        return $to;
    }
}

final class SqliteAgentRegistry implements IAgentRegistry
{
    /** @var PDO */ private $pdo;

    public function __construct(PDO $pdo)
    {
        $this->pdo = $pdo;
    }

    public function enabledAgents(): array
    {
        $rows = $this->pdo->query('SELECT * FROM agent_registry WHERE enabled = 1 ORDER BY name')->fetchAll();
        return array_map([$this, 'decode'], $rows);
    }

    public function agent(string $name): ?array
    {
        $stmt = $this->pdo->prepare('SELECT * FROM agent_registry WHERE name = ? AND enabled = 1');
        $stmt->execute([$name]);
        $row = $stmt->fetch();
        return $row ? $this->decode($row) : null;
    }

    /** Upserts the registry from config. Called by bin/migrate.php only. */
    public static function seed(PDO $pdo, array $agents): void
    {
        $pdo->exec('UPDATE agent_registry SET enabled = 0');
        $stmt = $pdo->prepare(
            'INSERT INTO agent_registry (name, display_name, description, operations, workspaces, write_workspaces, enabled)
             VALUES (?, ?, ?, ?, ?, ?, 1)
             ON CONFLICT (name) DO UPDATE SET display_name = excluded.display_name, description = excluded.description,
                 operations = excluded.operations, workspaces = excluded.workspaces,
                 write_workspaces = excluded.write_workspaces, enabled = 1');
        foreach ($agents as $agent) {
            $write = isset($agent['write_workspaces']) ? array_values($agent['write_workspaces']) : [];
            // a write workspace must also be a workspace the agent may use at all
            $write = array_values(array_intersect($write, array_values($agent['workspaces'])));
            $stmt->execute([$agent['name'], $agent['display_name'], $agent['description'],
                            json_encode(array_values($agent['operations'])), json_encode(array_values($agent['workspaces'])),
                            json_encode($write)]);
        }
    }

    private function decode(array $row): array
    {
        return [
            'name'         => $row['name'],
            'display_name' => $row['display_name'],
            'description'  => $row['description'],
            'operations'   => json_decode((string) $row['operations'], true) ?: [],
            'workspaces'   => json_decode((string) $row['workspaces'], true) ?: [],
            'write_workspaces' => isset($row['write_workspaces']) ? (json_decode((string) $row['write_workspaces'], true) ?: []) : [],
        ];
    }
}

final class SqliteAuditLogger implements IAuditLogger
{
    /** @var PDO */ private $pdo;
    /** @var IClock */ private $clock;

    public function __construct(PDO $pdo, IClock $clock)
    {
        $this->pdo   = $pdo;
        $this->clock = $clock;
    }

    public function record(string $actor, string $action, ?string $taskId, string $outcome, array $detail = []): void
    {
        $this->pdo->prepare('INSERT INTO audit_events (at, actor, action, task_id, outcome, detail) VALUES (?, ?, ?, ?, ?, ?)')
                  ->execute([$this->clock->now(), $actor, $action, $taskId, $outcome, json_encode($detail)]);
    }
}

final class SystemClock implements IClock
{
    public function now(): int
    {
        return time();
    }
}

final class RandomIds implements IIdGenerator
{
    public function newId(string $prefix): string
    {
        return $prefix . bin2hex(random_bytes(12));
    }

    public function newSecret(): string
    {
        return rtrim(strtr(base64_encode(random_bytes(32)), '+/', '-_'), '=');
    }
}
