<?php
declare(strict_types=1);

namespace AgentBridge;

use PDO;

/**
 * Who may do what with EG's printers. Same shape as ScannerScopes: a client's
 * scopes are the union of what its id, its label and '*' are granted, and an
 * unlisted client gets none. Pure.
 */
final class PrinterScopes
{
    const ALL = ['printer:list', 'printer:print', 'printer:status', 'printer:cancel'];

    /** @var array<string,string[]> */ private $map;

    public function __construct(array $map)
    {
        $this->map = $map;
    }

    /** @return string[] */
    public function of(Principal $who): array
    {
        if ($who->role !== 'client') {
            return [];
        }
        $granted = [];
        foreach ([$who->id, $who->label, '*'] as $key) {
            if (isset($this->map[$key]) && is_array($this->map[$key])) {
                $granted = array_merge($granted, $this->map[$key]);
            }
        }
        return array_values(array_intersect(self::ALL, $granted));
    }

    public function allows(Principal $who, string $scope): bool
    {
        return in_array($scope, $this->of($who), true);
    }
}

/**
 * The exact prompt each printer operation may carry. Nothing here is a path or
 * a command: a print prompt is a small JSON object with known keys only, and
 * the worker validates it again with Pydantic. Pure.
 */
final class PrintPromptShape
{
    const JOB_ID      = 't_[0-9a-f]{24}';
    const PRINTER_ID  = '/^[a-z0-9][a-z0-9_]{0,63}$/';
    const DOCUMENT_ID = '/^doc_[0-9a-f]{16}$/';
    const PAGE_RANGE  = '/^\d{1,3}(-\d{1,3})?(,\d{1,3}(-\d{1,3})?){0,9}$/';
    const KEYS        = ['kind', 'title', 'text', 'document_id', 'printer_id', 'options'];
    const OPTIONS     = ['copies', 'page_range', 'orientation', 'color', 'paper', 'duplex'];
    const MAX_TEXT    = 6000;
    const MAX_TITLE   = 120;
    const MAX_COPIES  = 10;

    /** Returns why the prompt is refused, or null when it has the right shape. */
    public static function problem(string $operation, string $prompt): ?string
    {
        switch ($operation) {
            case 'print_discover':
                return $prompt === 'discover' ? null : 'a discover task says discover and nothing else';
            case 'print_status':
                return preg_match('/^(job=' . self::JOB_ID . '|recent)$/', $prompt) ? null
                     : 'a status task says job=<job id> or recent, and nothing else';
            case 'print_cancel':
                return preg_match('/^cancel=' . self::JOB_ID . '$/', $prompt) ? null
                     : 'a cancel task says cancel=<job id> and nothing else';
            case 'print':
                return self::printProblem($prompt);
        }
        return 'not a printer operation';
    }

    private static function printProblem(string $prompt): ?string
    {
        $req = json_decode($prompt, true);
        if (!is_array($req) || array_keys($req) === range(0, count($req) - 1)) {
            return 'a print prompt is a JSON object';
        }
        if (array_diff(array_keys($req), self::KEYS)) {
            return 'a print prompt has only the keys ' . implode(', ', self::KEYS);
        }
        $kind = isset($req['kind']) ? $req['kind'] : null;
        if (!in_array($kind, ['text', 'document', 'test_page'], true)) {
            return 'kind is text, document or test_page';
        }
        if (isset($req['title']) && (!is_string($req['title']) || mb_strlen($req['title']) > self::MAX_TITLE)) {
            return 'title is at most ' . self::MAX_TITLE . ' characters';
        }
        $text = isset($req['text']) ? $req['text'] : null;
        $doc  = isset($req['document_id']) ? $req['document_id'] : null;
        if ($kind === 'text' && (!is_string($text) || trim($text) === '' || mb_strlen($text) > self::MAX_TEXT || $doc !== null)) {
            return 'a text print carries 1 to ' . self::MAX_TEXT . ' characters of text and no document_id';
        }
        if ($kind === 'document' && (!is_string($doc) || !preg_match(self::DOCUMENT_ID, $doc) || $text !== null)) {
            return 'a document print carries a document_id from printer_list_documents and no text';
        }
        if ($kind === 'test_page' && ($text !== null || $doc !== null)) {
            return 'the test page is fixed; it carries no text or document';
        }
        if (isset($req['printer_id']) && (!is_string($req['printer_id']) || !preg_match(self::PRINTER_ID, $req['printer_id']))) {
            return 'printer_id is one of the ids printer_list returns';
        }
        return isset($req['options']) ? self::optionsProblem($req['options']) : null;
    }

    private static function optionsProblem($options): ?string
    {
        if (!is_array($options) || array_diff(array_keys($options), self::OPTIONS)) {
            return 'options has only the keys ' . implode(', ', self::OPTIONS);
        }
        $fixed = ['orientation' => ['portrait', 'landscape'], 'color' => ['monochrome'], 'paper' => ['letter'],
                  'duplex' => ['simplex']];
        foreach ($fixed as $key => $allowed) {
            if (isset($options[$key]) && !in_array($options[$key], $allowed, true)) {
                return $key . ' is one of: ' . implode(', ', $allowed);
            }
        }
        if (isset($options['copies'])
            && (!is_int($options['copies']) || $options['copies'] < 1 || $options['copies'] > self::MAX_COPIES)) {
            return 'copies is 1 to ' . self::MAX_COPIES;
        }
        if (isset($options['page_range'])
            && (!is_string($options['page_range']) || !preg_match(self::PAGE_RANGE, $options['page_range']))) {
            return 'page_range looks like 1-3,5';
        }
        return null;
    }
}

/** What the gateway already knows about printer tasks: rows of its own tasks table. */
interface IPrinterJobs
{
    /** The client's newest task of an operation (with its result), optionally with an exact prompt. */
    public function latest(string $clientId, string $operation, ?string $prompt = null): ?array;
    /** The client's print tasks, newest first, each with its result. */
    public function recentPrints(string $clientId, int $limit): array;
    /** One of the client's print tasks with its result, or null when it is not theirs or not a print. */
    public function printJob(string $clientId, string $id): ?array;
    public function openPrints(string $clientId): int;
    public function printsSince(string $clientId, int $since): int;
    /** A print with this exact prompt since $since that has not failed or been cancelled. */
    public function duplicate(string $clientId, string $prompt, int $since): ?string;
}

final class SqlitePrinterJobs implements IPrinterJobs
{
    const WITH_RESULT = 'SELECT t.*, r.ok AS result_ok, r.summary AS result_summary, r.details AS result_details,
                                r.completed_at AS result_completed_at
                         FROM tasks t LEFT JOIN task_results r ON r.task_id = t.id';

    /** @var PDO */ private $pdo;

    public function __construct(PDO $pdo)
    {
        $this->pdo = $pdo;
    }

    public function latest(string $clientId, string $operation, ?string $prompt = null): ?array
    {
        $sql  = self::WITH_RESULT . " WHERE t.client_id = ? AND t.agent = 'printer' AND t.operation = ?"
              . ($prompt === null ? '' : ' AND t.prompt = ?') . ' ORDER BY t.created_at DESC, t.rowid DESC LIMIT 1';
        $stmt = $this->pdo->prepare($sql);
        $stmt->execute($prompt === null ? [$clientId, $operation] : [$clientId, $operation, $prompt]);
        $row = $stmt->fetch();
        return $row ?: null;
    }

    public function recentPrints(string $clientId, int $limit): array
    {
        $stmt = $this->pdo->prepare(self::WITH_RESULT . " WHERE t.client_id = ? AND t.agent = 'printer' AND t.operation = 'print'"
                                    . ' ORDER BY t.created_at DESC, t.rowid DESC LIMIT ' . max(1, min(50, $limit)));
        $stmt->execute([$clientId]);
        return $stmt->fetchAll();
    }

    public function printJob(string $clientId, string $id): ?array
    {
        if (!preg_match('/^' . PrintPromptShape::JOB_ID . '$/', $id)) {
            return null;
        }
        $stmt = $this->pdo->prepare(self::WITH_RESULT . " WHERE t.id = ? AND t.client_id = ? AND t.agent = 'printer'"
                                    . " AND t.operation = 'print'");
        $stmt->execute([$id, $clientId]);
        $row = $stmt->fetch();
        return $row ?: null;
    }

    public function openPrints(string $clientId): int
    {
        $stmt = $this->pdo->prepare("SELECT COUNT(*) FROM tasks WHERE client_id = ? AND agent = 'printer' AND operation = 'print'"
                                    . " AND state IN ('queued', 'running')");
        $stmt->execute([$clientId]);
        return (int) $stmt->fetchColumn();
    }

    public function printsSince(string $clientId, int $since): int
    {
        $stmt = $this->pdo->prepare("SELECT COUNT(*) FROM tasks WHERE client_id = ? AND agent = 'printer' AND operation = 'print'"
                                    . ' AND created_at >= ?');
        $stmt->execute([$clientId, $since]);
        return (int) $stmt->fetchColumn();
    }

    public function duplicate(string $clientId, string $prompt, int $since): ?string
    {
        $stmt = $this->pdo->prepare("SELECT id FROM tasks WHERE client_id = ? AND agent = 'printer' AND operation = 'print'"
                                    . " AND prompt = ? AND created_at >= ? AND state NOT IN ('failed', 'cancelled')"
                                    . ' ORDER BY created_at DESC LIMIT 1');
        $stmt->execute([$clientId, $prompt, $since]);
        $id = $stmt->fetchColumn();
        return $id === false ? null : (string) $id;
    }
}

/**
 * Consulted by PolicyAuthorizer for every printer task, REST or MCP alike.
 * Each operation needs its own scope and its exact prompt shape. A print
 * additionally meets three limits: at most $maxOpen unfinished prints (409),
 * at most $maxPerHour an hour (429), and the same document twice within ten
 * minutes is refused as a duplicate (409) -- a replay of the same idempotency
 * key is let through instead, so QueueTaskPhase returns the original task.
 */
final class PrintGuard
{
    const SCOPE_OF       = ['print' => 'printer:print', 'print_cancel' => 'printer:cancel',
                            'print_status' => 'printer:status', 'print_discover' => 'printer:list'];
    const DUPLICATE_SECS = 600;

    /** @var PrinterScopes */   private $scopes;
    /** @var IPrinterJobs */    private $jobs;
    /** @var ITaskRepository */ private $tasks;
    /** @var IClock */          private $clock;
    /** @var int */             private $maxPerHour;
    /** @var int */             private $maxOpen;

    public function __construct(PrinterScopes $scopes, IPrinterJobs $jobs, ITaskRepository $tasks, IClock $clock,
                                int $maxPerHour, int $maxOpen)
    {
        $this->scopes     = $scopes;
        $this->jobs       = $jobs;
        $this->tasks      = $tasks;
        $this->clock      = $clock;
        $this->maxPerHour = $maxPerHour;
        $this->maxOpen    = $maxOpen;
    }

    public static function handles(string $operation): bool
    {
        return isset(self::SCOPE_OF[$operation]);
    }

    public function check(Principal $who, array $envelope): ?string
    {
        $operation = $envelope['operation'];
        if (!self::handles($operation)) {
            return 'not a printer operation';
        }
        if (!$this->scopes->allows($who, self::SCOPE_OF[$operation])) {
            return 'this client may not ' . $operation . ' (' . self::SCOPE_OF[$operation] . ')';
        }
        $problem = PrintPromptShape::problem($operation, (string) $envelope['prompt']);
        if ($problem !== null) {
            return $problem;
        }
        if ($operation !== 'print') {
            return null;
        }
        if (isset($envelope['idempotency_key'])
            && $this->tasks->findByIdempotency($who->id, $envelope['idempotency_key']) !== null) {
            return null;
        }
        $now = $this->clock->now();
        $dup = $this->jobs->duplicate($who->id, (string) $envelope['prompt'], $now - self::DUPLICATE_SECS);
        if ($dup !== null) {
            throw new BridgeError(409, 'duplicate_print', 'the same print was sent as job ' . $dup
                                  . ' in the last ten minutes; check printer_get_status before sending it again');
        }
        if ($this->jobs->openPrints($who->id) >= $this->maxOpen) {
            throw new BridgeError(409, 'printer_busy', 'at most ' . $this->maxOpen . ' print jobs may be unfinished at once');
        }
        if ($this->jobs->printsSince($who->id, $now - 3600) >= $this->maxPerHour) {
            throw new BridgeError(429, 'rate_limited', 'at most ' . $this->maxPerHour . ' bridge prints an hour');
        }
        return null;
    }
}
