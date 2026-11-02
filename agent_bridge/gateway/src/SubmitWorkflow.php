<?php
declare(strict_types=1);

namespace AgentBridge;

/**
 * The submit side of the bridge as visible phases:
 *   ReceiveTask -> AuthenticateTask -> ValidateTask -> AuthorizeTask -> QueueTask
 * Each phase does one thing and throws BridgeError to stop. The phases hold no
 * business logic of their own; they call the interfaces they were given.
 */
final class SubmitWorkflow
{
    /** @var ISubmitPhase[] */ private $phases;
    /** @var IAuditLogger */   private $audit;

    public function __construct(array $phases, IAuditLogger $audit)
    {
        $this->phases = $phases;
        $this->audit  = $audit;
    }

    public function run(Request $request): SubmitContext
    {
        $context = new SubmitContext($request);
        foreach ($this->phases as $phase) {
            try {
                $phase->run($context);
            } catch (BridgeError $e) {
                $actor = $context->principal ? $context->principal->id : 'anonymous';
                $this->audit->record($actor, 'submit', null, 'refused',
                                     ['phase' => $phase->name(), 'code' => $e->errorCode]);
                throw $e;
            }
            $context->phasesRun[] = $phase->name();
            if ($context->replayed) {
                break;
            }
        }
        return $context;
    }
}

final class ReceiveTaskPhase implements ISubmitPhase
{
    /** @var int */ private $maxBody;

    public function __construct(int $maxBody)
    {
        $this->maxBody = $maxBody;
    }

    public function name(): string { return 'ReceiveTask'; }

    public function run(SubmitContext $context): void
    {
        if (strlen($context->request->body) > $this->maxBody) {
            throw new BridgeError(413, 'too_large', 'request body is larger than ' . $this->maxBody . ' bytes');
        }
        $body = json_decode($context->request->body, true);
        if (!is_array($body) || array_keys($body) === range(0, count($body) - 1) && $body !== []) {
            throw new BridgeError(400, 'bad_json', 'body must be a JSON object');
        }
        $context->body = $body;
    }
}

final class AuthenticateTaskPhase implements ISubmitPhase
{
    /** @var ITaskAuthenticator */ private $auth;

    public function __construct(ITaskAuthenticator $auth)
    {
        $this->auth = $auth;
    }

    public function name(): string { return 'AuthenticateTask'; }

    public function run(SubmitContext $context): void
    {
        $principal = $this->auth->authenticate($context->request);
        if ($principal === null) {
            throw new BridgeError(401, 'unauthenticated', 'a valid bearer token is required');
        }
        $context->principal = $principal;
    }
}

final class ValidateTaskPhase implements ISubmitPhase
{
    /** @var ITaskValidator */ private $validator;

    public function __construct(ITaskValidator $validator)
    {
        $this->validator = $validator;
    }

    public function name(): string { return 'ValidateTask'; }

    public function run(SubmitContext $context): void
    {
        $problems = $this->validator->validate($context->body);
        if ($problems) {
            throw new BridgeError(422, 'invalid_task', implode('; ', $problems));
        }
    }
}

final class AuthorizeTaskPhase implements ISubmitPhase
{
    /** @var ITaskAuthorizer */ private $authorizer;

    public function __construct(ITaskAuthorizer $authorizer)
    {
        $this->authorizer = $authorizer;
    }

    public function name(): string { return 'AuthorizeTask'; }

    public function run(SubmitContext $context): void
    {
        $decision = $this->authorizer->authorize($context->principal, $context->body);
        if (!$decision->allowed) {
            throw new BridgeError(403, 'not_allowed', $decision->reason);
        }
        $context->decision = $decision;
    }
}

final class QueueTaskPhase implements ISubmitPhase
{
    /** @var ITaskRepository */ private $tasks;
    /** @var IAuditLogger */    private $audit;
    /** @var int */             private $maxAttempts;

    public function __construct(ITaskRepository $tasks, IAuditLogger $audit, int $maxAttempts)
    {
        $this->tasks       = $tasks;
        $this->audit       = $audit;
        $this->maxAttempts = $maxAttempts;
    }

    public function name(): string { return 'QueueTask'; }

    public function run(SubmitContext $context): void
    {
        $body = $context->body;
        $key  = isset($body['idempotency_key']) ? $body['idempotency_key'] : null;
        if ($key !== null) {
            $existing = $this->tasks->findByIdempotency($context->principal->id, $key);
            if ($existing !== null) {
                $this->assertSameRequest($existing, $body, $context->decision->effectiveApprovalMode);
                $context->task     = $existing;
                $context->replayed = true;
                $this->audit->record($context->principal->id, 'submit', $existing['id'], 'replayed');
                return;
            }
        }
        $task = $this->tasks->insert([
            'client_id'       => $context->principal->id,
            'idempotency_key' => $key,
            'schema_version'  => $body['schema_version'],
            'task_type'       => $body['task_type'],
            'agent'           => $body['agent'],
            'workspace'       => $body['workspace'],
            'operation'       => $body['operation'],
            'requested_mode'  => isset($body['approval_mode']) ? $body['approval_mode'] : $context->decision->effectiveApprovalMode,
            'approval_mode'   => $context->decision->effectiveApprovalMode,
            'prompt'          => $body['prompt'],
            // a write task is never handed out twice: an expired lease after partial
            // edits fails, so the worker's report of what changed is the only next step
            'max_attempts'    => PolicyAuthorizer::runsOnce($context->decision->effectiveApprovalMode) ? 1 : $this->maxAttempts,
        ]);
        $context->task = $task;
        $this->audit->record($context->principal->id, 'submit', $task['id'], 'queued',
                             ['agent' => $task['agent'], 'operation' => $task['operation'], 'workspace' => $task['workspace']]);
    }

    /** The same key with a different request is a client bug, not a replay. */
    private function assertSameRequest(array $existing, array $body, string $effectiveMode): void
    {
        foreach (['agent', 'workspace', 'operation', 'prompt'] as $field) {
            if ((string) $existing[$field] !== (string) $body[$field]) {
                throw new BridgeError(409, 'idempotency_conflict', 'idempotency_key was already used for a different task');
            }
        }
        if ((string) $existing['approval_mode'] !== $effectiveMode) {
            throw new BridgeError(409, 'idempotency_conflict', 'idempotency_key was already used with a different approval_mode');
        }
    }
}
