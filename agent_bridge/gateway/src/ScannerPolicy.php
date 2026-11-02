<?php
declare(strict_types=1);

namespace AgentBridge;

/**
 * Who may do what with the scanners. ScannerScopes is pure: a client's scopes
 * are the union of what its id, its label and '*' are granted, and an
 * unlisted client gets none.
 */
final class ScannerScopes
{
    const ALL = ['scanner:start', 'scanner:status', 'scanner:list', 'scanner:document:read', 'scanner:verdict:read'];

    /** @var array<string,string[]> principal id, exact label, or '*' => scopes */ private $map;

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
 * Consulted by PolicyAuthorizer for every scan task, REST or MCP alike: the
 * caller must hold scanner:start, the prompt must name exactly one known
 * scanner, a scanner already busy refuses a second start (409), and each
 * client gets a bounded number of starts an hour (429). A replay of an
 * existing idempotency key is let through, so QueueTaskPhase can return it.
 */
final class ScanStartGuard
{
    const PROMPT = '/^scanner=(window|freezer)$/';

    /** @var ScannerScopes */   private $scopes;
    /** @var IScanStore */      private $scans;
    /** @var ITaskRepository */ private $tasks;
    /** @var IClock */          private $clock;
    /** @var int */             private $maxPerHour;

    public function __construct(ScannerScopes $scopes, IScanStore $scans, ITaskRepository $tasks, IClock $clock, int $maxPerHour)
    {
        $this->scopes     = $scopes;
        $this->scans      = $scans;
        $this->tasks      = $tasks;
        $this->clock      = $clock;
        $this->maxPerHour = $maxPerHour;
    }

    public function check(Principal $who, array $envelope): ?string
    {
        if (!$this->scopes->allows($who, 'scanner:start')) {
            return 'this client may not start scans (scanner:start)';
        }
        if (!preg_match(self::PROMPT, $envelope['prompt'])) {
            return 'a scan prompt is exactly scanner=window or scanner=freezer';
        }
        if (isset($envelope['idempotency_key'])
            && $this->tasks->findByIdempotency($who->id, $envelope['idempotency_key']) !== null) {
            return null;
        }
        $active = $this->scans->activeStart($envelope['prompt']);
        if ($active !== null) {
            throw new BridgeError(409, 'scanner_busy', 'that scanner is already running bridge task ' . $active['id']);
        }
        if ($this->scans->startsSince($who->id, $this->clock->now() - 3600) >= $this->maxPerHour) {
            throw new BridgeError(429, 'rate_limited', 'at most ' . $this->maxPerHour . ' bridge scans an hour');
        }
        return null;
    }
}
