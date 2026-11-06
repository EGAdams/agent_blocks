<?php
declare(strict_types=1);

namespace AgentBridge;

use PDO;

/** Scopes and sender are bound to authenticated ids by server configuration. */
final class ClientScopes
{
    private $bindings;
    private $protectedAgents;
    private $protectedWorkspaces;
    public function __construct(array $bindings = [], array $protectedAgents = [], array $protectedWorkspaces = [])
    {
        $this->bindings = $bindings;
        $this->protectedAgents = $protectedAgents;
        $this->protectedWorkspaces = $protectedWorkspaces;
    }
    public function sender(string $id): string
    {
        return isset($this->bindings[$id]['sender']) ? (string) $this->bindings[$id]['sender']
            : 'untrusted-bridge';
    }
    public function refusal(Principal $principal, array $envelope): ?string
    {
        $scope = $this->bindings[$principal->id] ?? null;
        if ($scope === null) {
            return (in_array($envelope['agent'], $this->protectedAgents, true)
                    || in_array($envelope['workspace'], $this->protectedWorkspaces, true))
                ? 'authenticated identity is not authorized for this agent' : null;
        }
        foreach (['operations' => 'operation', 'workspaces' => 'workspace', 'agents' => 'agent'] as $plural => $field) {
            if (isset($scope[$plural]) && !in_array($envelope[$field], $scope[$plural], true)) {
                return 'authenticated identity is not authorized for this ' . $field;
            }
        }
        return null;
    }
}

/** Shape checks only. Nothing here decides whether a task is allowed. */
final class EnvelopeValidator implements ITaskValidator
{
    const OPERATIONS = ['inspect', 'search_memory', 'explain', 'implement', 'propose_changes', 'modify_code', 'run_tests', 'scan',
                          'print', 'print_cancel', 'print_status', 'print_discover'];
    const MODES      = ['read_only', 'workspace_write', 'propose', 'approved_change', 'device_control'];
    const FIELDS     = ['schema_version', 'task_type', 'agent', 'workspace', 'operation', 'approval_mode', 'prompt', 'idempotency_key'];
    const REQUIRED   = ['schema_version', 'task_type', 'agent', 'workspace', 'operation', 'prompt'];

    public function validate(array $body): array
    {
        $problems = [];
        foreach (array_keys($body) as $key) {
            if (!in_array($key, self::FIELDS, true)) {
                $problems[] = 'unknown field: ' . (is_string($key) ? substr($key, 0, 40) : '#');
            }
        }
        foreach (self::REQUIRED as $key) {
            if (!array_key_exists($key, $body)) {
                $problems[] = 'missing field: ' . $key;
            } elseif (!is_string($body[$key])) {
                $problems[] = $key . ' must be a string';
            }
        }
        if ($problems) {
            return $problems;
        }
        if ($body['schema_version'] !== '1.0') {
            $problems[] = 'schema_version must be "1.0"';
        }
        if ($body['task_type'] !== 'agent_task') {
            $problems[] = 'task_type must be "agent_task"';
        }
        if (!preg_match('/^[a-z][a-z0-9-]{0,40}$/', $body['agent'])) {
            $problems[] = 'agent must be a lower-case name';
        }
        if (!preg_match('/^[a-z][a-z0-9_-]{0,40}$/', $body['workspace'])) {
            $problems[] = 'workspace must be a workspace name, not a path';
        }
        if (!in_array($body['operation'], self::OPERATIONS, true)) {
            $problems[] = 'operation must be one of: ' . implode(', ', self::OPERATIONS);
        }
        if (isset($body['approval_mode']) && (!is_string($body['approval_mode']) || !in_array($body['approval_mode'], self::MODES, true))) {
            $problems[] = 'approval_mode must be one of: ' . implode(', ', self::MODES);
        }
        $length = function_exists('mb_strlen') ? mb_strlen($body['prompt'], 'UTF-8') : strlen($body['prompt']);
        if (trim($body['prompt']) === '' || $length > 8000) {
            $problems[] = 'prompt must be 1 to 8000 characters';
        }
        if (!preg_match('//u', $body['prompt'])) {
            $problems[] = 'prompt must be valid UTF-8';
        }
        if (isset($body['idempotency_key'])
            && (!is_string($body['idempotency_key']) || !preg_match('/^[A-Za-z0-9._:-]{1,128}$/', $body['idempotency_key']))) {
            $problems[] = 'idempotency_key must be 1 to 128 of A-Z a-z 0-9 . _ : -';
        }
        return $problems;
    }
}

/**
 * Server-side policy. The client's approval_mode is a request; this decides.
 *
 * The mode is a property of the operation (operation_modes), never of what the
 * client asked for: inspect/explain/search_memory run read_only, implement runs
 * workspace_write. A client that names a different mode is refused, not
 * upgraded or downgraded. A write mode also needs the workspace to be in that
 * agent's write_workspaces, which only the gateway's own config can set.
 */
final class PolicyAuthorizer implements ITaskAuthorizer
{
    const WRITE_MODES = ['workspace_write'];
    /** Modes whose task must never run twice: a write, or a physical device. */
    const RUN_ONCE_MODES = ['workspace_write', 'device_control'];

    /** @var IAgentRegistry */       private $agents;
    /** @var string[] */             private $allowedOperations;
    /** @var string[] */             private $allowedModes;
    /** @var array<string,string> */ private $operationModes;
    /** @var ScanStartGuard|null scan is refused outright without one */ private $scans;
    /** @var PrintGuard|null every printer operation is refused outright without one */ private $prints;
    private $clientScopes;

    public function __construct(IAgentRegistry $agents, array $allowedOperations, array $allowedModes, array $operationModes,
                                ?ScanStartGuard $scans = null, ?PrintGuard $prints = null, ?ClientScopes $clientScopes = null)
    {
        $this->agents            = $agents;
        $this->allowedOperations = $allowedOperations;
        $this->allowedModes      = $allowedModes;
        $this->operationModes    = $operationModes;
        $this->scans             = $scans;
        $this->prints            = $prints;
        $this->clientScopes      = $clientScopes ?: new ClientScopes();
    }

    public static function runsOnce(string $mode): bool
    {
        return in_array($mode, self::RUN_ONCE_MODES, true);
    }

    public static function isWriteMode(string $mode): bool
    {
        return in_array($mode, self::WRITE_MODES, true);
    }

    public function authorize(Principal $principal, array $envelope): Decision
    {
        if ($principal->role !== 'client') {
            return new Decision(false, 'only client tokens may submit tasks', 'read_only');
        }
        $refusal = $this->clientScopes->refusal($principal, $envelope);
        if ($refusal !== null) {
            return new Decision(false, $refusal, 'read_only');
        }
        $agent = $this->agents->agent($envelope['agent']);
        if ($agent === null) {
            return new Decision(false, 'no enabled agent named ' . $envelope['agent'], 'read_only');
        }
        $operation = $envelope['operation'];
        if (!in_array($operation, $this->allowedOperations, true)) {
            return new Decision(false, 'operation ' . $operation . ' is not enabled on this gateway', 'read_only');
        }
        if (!isset($this->operationModes[$operation])) {
            return new Decision(false, 'operation ' . $operation . ' has no execution mode on this gateway', 'read_only');
        }
        if (!in_array($operation, $agent['operations'], true)) {
            return new Decision(false, $agent['display_name'] . ' does not accept ' . $operation, 'read_only');
        }
        if (!in_array($envelope['workspace'], $agent['workspaces'], true)) {
            return new Decision(false, $agent['display_name'] . ' is not allowed in workspace ' . $envelope['workspace'], 'read_only');
        }
        $effective = (string) $this->operationModes[$operation];
        $requested = isset($envelope['approval_mode']) ? $envelope['approval_mode'] : $effective;
        if ($requested !== $effective) {
            return new Decision(false, 'operation ' . $operation . ' runs ' . $effective . ', not ' . $requested, 'read_only');
        }
        if (!in_array($effective, $this->allowedModes, true)) {
            return new Decision(false, 'approval_mode ' . $effective . ' is not granted on this gateway', 'read_only');
        }
        if (self::isWriteMode($effective)
            && !in_array($envelope['workspace'], isset($agent['write_workspaces']) ? $agent['write_workspaces'] : [], true)) {
            return new Decision(false, $agent['display_name'] . ' may not write in workspace ' . $envelope['workspace'], 'read_only');
        }
        if ($operation === 'scan') {
            $refusal = $this->scans === null ? 'scanner control is not configured on this gateway'
                                             : $this->scans->check($principal, $envelope);
            if ($refusal !== null) {
                return new Decision(false, $refusal, 'read_only');
            }
        }
        if (PrintGuard::handles($operation)) {
            $refusal = $this->prints === null ? 'printing is not configured on this gateway'
                                              : $this->prints->check($principal, $envelope);
            if ($refusal !== null) {
                return new Decision(false, $refusal, 'read_only');
            }
        }
        return new Decision(true, 'allowed', $effective);
    }
}

/** Bearer tokens, stored only as sha256. Roles are client and worker. */
final class TokenAuthenticator implements ITaskAuthenticator
{
    /** @var PDO */ private $pdo;

    public function __construct(PDO $pdo)
    {
        $this->pdo = $pdo;
    }

    public function authenticate(Request $request): ?Principal
    {
        if (!preg_match('/^Bearer\s+([A-Za-z0-9_-]{20,200})$/', trim($request->header('authorization')), $m)) {
            return null;
        }
        $stmt = $this->pdo->prepare('SELECT id, role, label FROM api_tokens WHERE token_hash = ? AND revoked = 0');
        $stmt->execute([hash('sha256', $m[1])]);
        $row = $stmt->fetch();
        return $row ? new Principal((string) $row['id'], (string) $row['role'], (string) $row['label']) : null;
    }

    /** Creates a token, stores its hash, and returns the token. Used by bin/issue_token.php. */
    public static function issue(PDO $pdo, IIdGenerator $ids, IClock $clock, string $role, string $label): array
    {
        if (!in_array($role, ['client', 'worker'], true)) {
            throw new \InvalidArgumentException('role must be client or worker');
        }
        $token = $ids->newSecret();
        $id    = $ids->newId('k_');
        $pdo->prepare('INSERT INTO api_tokens (id, role, token_hash, label, created_at) VALUES (?, ?, ?, ?, ?)')
            ->execute([$id, $role, hash('sha256', $token), $label, $clock->now()]);
        return ['id' => $id, 'token' => $token];
    }
}
