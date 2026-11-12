<?php
declare(strict_types=1);

namespace AgentBridge;

/**
 * Workspaces that only one machine's worker may run.
 *
 * Config `worker_pins` maps a worker token id to the workspaces pinned to it:
 *
 *     'worker_pins' => ['w_<token id>' => ['cashy-r46']],
 *
 * A pinned worker is offered and may claim only its own workspaces. Every other worker is never offered
 * them and may not claim them, so a task for "Cashy on R46" can only ever run on R46 -- it is never
 * picked up, and failed, by the home worker that has no such agent. Keyed by token id (unforgeable,
 * like `client_scopes`), never by the worker_id label a worker reports about itself.
 */
final class WorkerPins
{
    /** @var array<string, string[]> */
    private $pins = [];

    public function __construct(array $pins = [])
    {
        foreach ($pins as $tokenId => $workspaces) {
            $list = array_values(array_filter((array) $workspaces, 'is_string'));
            if (is_string($tokenId) && $list) {
                $this->pins[$tokenId] = $list;
            }
        }
    }

    /** The workspaces pinned to this worker, or null when it is an ordinary worker. */
    public function workspacesFor(Principal $who): ?array
    {
        return $this->pins[$who->id] ?? null;
    }

    /** Every workspace pinned to some worker. */
    public function pinned(): array
    {
        return $this->pins ? array_values(array_unique(array_merge(...array_values($this->pins)))) : [];
    }

    public function mayRun(Principal $who, string $workspace): bool
    {
        $own = $this->workspacesFor($who);
        return $own === null ? !in_array($workspace, $this->pinned(), true) : in_array($workspace, $own, true);
    }
}
