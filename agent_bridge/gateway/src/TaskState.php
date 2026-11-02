<?php
declare(strict_types=1);

namespace AgentBridge;

/**
 * The task lifecycle as one table. The store asks this before every
 * transition, and also writes `WHERE state = <from>`, so two racing
 * workers cannot both move the same task.
 *
 *   queued  -> running (claim), cancelled, failed (no attempts left)
 *   running -> queued (lease expired or retryable failure),
 *              succeeded, failed, cancelled
 *   succeeded, failed, cancelled are terminal.
 */
final class TaskState
{
    const QUEUED    = 'queued';
    const RUNNING   = 'running';
    const SUCCEEDED = 'succeeded';
    const FAILED    = 'failed';
    const CANCELLED = 'cancelled';

    private const MOVES = [
        self::QUEUED    => [self::RUNNING, self::CANCELLED, self::FAILED],
        self::RUNNING   => [self::QUEUED, self::SUCCEEDED, self::FAILED, self::CANCELLED],
        self::SUCCEEDED => [],
        self::FAILED    => [],
        self::CANCELLED => [],
    ];

    public static function canMove(string $from, string $to): bool
    {
        return isset(self::MOVES[$from]) && in_array($to, self::MOVES[$from], true);
    }

    public static function isTerminal(string $state): bool
    {
        return isset(self::MOVES[$state]) && self::MOVES[$state] === [];
    }

    public static function all(): array
    {
        return array_keys(self::MOVES);
    }
}
