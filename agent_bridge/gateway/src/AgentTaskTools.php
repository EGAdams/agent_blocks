<?php
declare(strict_types=1);

namespace AgentBridge;

/**
 * Template Method for a family of named MCP tools that each submit one task to one Bridge agent.
 *
 * A connector reading its tool list has to see a named tool or it concludes the capability does not exist, so
 * each capability gets its own tools -- but what every such tool does is the same: check the caller may use it,
 * shape the arguments, submit a task through ClientTasks->submit with the caller's own credentials (so
 * PolicyAuthorizer and ClientScopes see it as any other task), wait a little for the worker, and report the
 * verified result with diagnostics. That skeleton lives here; a subclass supplies the table and the words:
 *
 *   agent(), workspace()   which Bridge agent and workspace the tasks go to
 *   tools()                tool => [op, task operation, allowed fields, required fields]; op null = get_result
 *   requestFor()           the worker prompt for one call (after the fields are shaped)
 *   detailKeys()           what a result may carry back from the worker's details
 *   definitionTable()      each tool's title, description, input schema and annotations
 *   pending(), notDone()   what to tell the caller when a result is not ready, or ready but not verified
 *
 * RelayTools and BrowserTools predate this class and keep their own copies of the skeleton; NotesTools is the
 * first subclass.
 */
abstract class AgentTaskTools implements IToolFamily
{
    const MAX_VALUE = 500;

    /** @var ClientTasks */  protected $client;
    /** @var ClientScopes */ protected $scopes;
    /** @var IAuditLogger */ protected $audit;
    /** @var array<string,bool> task operation => granted by the gateway */ protected $granted;
    /** @var int */          protected $waitSeconds;
    /** @var callable(int):void */ protected $sleep;

    public function __construct(ClientTasks $client, ClientScopes $scopes, IAuditLogger $audit, array $grants,
                                int $waitSeconds = 30, ?callable $sleep = null)
    {
        $this->client      = $client;
        $this->scopes      = $scopes;
        $this->audit       = $audit;
        $this->granted     = [
            'inspect'   => in_array('inspect', $grants['operations'], true) && in_array('read_only', $grants['modes'], true),
            'implement' => in_array('implement', $grants['operations'], true) && in_array('workspace_write', $grants['modes'], true),
        ];
        $this->waitSeconds = max(0, $waitSeconds);
        $this->sleep       = $sleep ?: function (int $s) { sleep($s); };
    }

    abstract protected function agent(): string;
    abstract protected function workspace(): string;
    /** @return array<string,array{0:?string,1:string,2:string[],3:string[]}> */
    abstract protected function tools(): array;
    /** The worker prompt for one call; throw BridgeError(422) for a shape the worker would refuse anyway. */
    abstract protected function requestFor(string $op, array $args, array $allowed, array $required): array;
    /** @return string[] */
    abstract protected function detailKeys(): array;
    abstract protected function definitionTable(): array;
    abstract protected function pending(): string;
    abstract protected function notDone(array $details): string;

    /** Hook: the sentences the MCP initialize instructions carry when these tools are offered. */
    public function instructions(): string
    {
        return '';
    }

    public function handles(string $name): bool
    {
        return isset($this->tools()[$name]);
    }

    public function offered(Principal $who): bool
    {
        return (bool) $this->definitions($who);
    }

    public function definitions(Principal $who): array
    {
        if (!$this->agentListed()) {
            return [];
        }
        $out = [];
        foreach ($this->definitionTable() as $name => $tool) {
            if ($this->permitted($who, $name)) {
                $out[] = ['name' => $name] + $tool;
            }
        }
        return $out;
    }

    public function call(Request $r, Principal $who, string $name, array $args): array
    {
        if (!$this->permitted($who, $name) || !$this->agentListed()) {
            $this->audit->record($who->id, $name, null, 'denied');
            return self::error('not_allowed: this client may not use ' . $name);
        }
        try {
            list($op, $operation, $fields, $required) = $this->tools()[$name];
            if ($op === null) {
                $id    = isset($args['task_id']) && is_string($args['task_id']) ? $args['task_id'] : '';
                $first = $this->client->result($who, $id);
                if ($first['workspace'] !== $this->workspace()) {
                    throw new BridgeError(404, 'not_found', 'no such ' . $this->workspace()
                        . ' task (use get_agent_task_result for other tasks)');
                }
                return self::ok($this->await($who, $id, $first));
            }
            $request = $this->prepare($who, $op, $this->requestFor($op, $args, $fields, $required));
            $view    = $this->submit($r, $operation, $request);
            $this->audit->record($who->id, $name, $view['task_id'], 'submitted');
            return self::ok($this->await($who, $view['task_id'], null));
        } catch (BridgeError $e) {
            $this->audit->record($who->id, $name, null, $e->errorCode);
            return self::error($e->errorCode . ': ' . $e->getMessage());
        }
    }

    /** Whether this caller may use the named tool (another family may lend its permission, as uploads do). */
    public function permits(Principal $who, string $name): bool
    {
        return $this->permitted($who, $name) && $this->agentListed();
    }

    /**
     * Hook: the last look at a request before it is queued, with the caller known -- for a check that depends on
     * who is asking (an upload must be the caller's own). Default: the request as shaped.
     */
    protected function prepare(Principal $who, string $op, array $request): array
    {
        return $request;
    }

    private function permitted(Principal $who, string $name): bool
    {
        $tools = $this->tools();
        if (!isset($tools[$name]) || $who->role !== 'client') {
            return false;
        }
        $operation = $tools[$name][1];
        if (!$this->granted[$operation]) {
            return false;
        }
        return $this->scopes->refusal($who, ['agent' => $this->agent(), 'workspace' => $this->workspace(),
                                             'operation' => $operation]) === null;
    }

    private function agentListed(): bool
    {
        foreach ($this->client->agents()['agents'] as $agent) {
            if (isset($agent['name']) && $agent['name'] === $this->agent()) {
                return true;
            }
        }
        return false;
    }

    private function submit(Request $r, string $operation, array $request): array
    {
        $envelope = ['schema_version' => '1.0', 'task_type' => 'agent_task', 'agent' => $this->agent(),
                     'workspace' => $this->workspace(), 'operation' => $operation,
                     'approval_mode' => $operation === 'inspect' ? 'read_only' : 'workspace_write',
                     'prompt' => (string) json_encode($request, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE)];
        $inner = new Request('POST', '/tasks', ['authorization' => $r->header('authorization')], (string) json_encode($envelope));
        list(, $view) = $this->client->submit($inner);
        return $view;
    }

    /** Polls the task's result for up to waitSeconds; returns what is known either way. */
    private function await(Principal $who, string $id, ?array $first): array
    {
        $result = $first ?: $this->client->result($who, $id);
        for ($waited = 0; !$result['ready'] && !self::terminal($result['state']) && $waited < $this->waitSeconds; $waited++) {
            ($this->sleep)(1);
            $result = $this->client->result($who, $id);
        }
        return $this->view($who, $id, $result);
    }

    private static function terminal(string $state): bool
    {
        return !in_array($state, [TaskState::QUEUED, TaskState::RUNNING], true);
    }

    private function view(Principal $who, string $id, array $result): array
    {
        if (!$result['ready']) {
            $out = ['task_id' => $id, 'ready' => false, 'state' => $result['state'], 'verified' => false,
                    'next' => $this->pending()];
            if (self::terminal($result['state'])) {
                $out['next']        = 'The task ended without a result: ' . (string) $result['last_error'];
                $out['diagnostics'] = $this->steps($who, $id);
            }
            return $out;
        }
        $details  = is_array($result['details']) ? $result['details'] : (array) $result['details'];
        $verified = $this->verified((bool) $result['ok'], $details);
        $out = ['task_id' => $id, 'ready' => true, 'state' => $result['state'], 'ok' => (bool) $result['ok'],
                'verified' => $verified, 'line' => (string) $result['summary']];
        foreach ($this->detailKeys() as $key) {
            if (array_key_exists($key, $details)) {
                $out[$key] = $details[$key];
            }
        }
        if (!$verified) {
            $out['diagnostics'] = $this->steps($who, $id);
            $out['next']        = $this->notDone($details);
        }
        return $out;
    }

    /** Hook: whether a finished result counts as verified. Default: ok and the worker said verified=true. */
    protected function verified(bool $ok, array $details): bool
    {
        return $ok && isset($details['verified']) && $details['verified'] === true;
    }

    /** The task's recorded steps (queued, claimed, the worker's phases, the end), oldest first. */
    private function steps(Principal $who, string $id): array
    {
        $status = $this->client->status($who, $id);
        return array_map(function ($e) {
            return $e['kind'] . ': ' . mb_substr((string) $e['message'], 0, 200);
        }, isset($status['recent_events']) ? $status['recent_events'] : []);
    }

    /** Short scalar values (or true for a switch) for the allowed fields only; then the required ones checked. */
    protected static function shape(array $args, array $allowed, array $required, array $switches, array $limits,
                                    string $what): array
    {
        $out = [];
        foreach ($allowed as $key) {
            if (!array_key_exists($key, $args) || $args[$key] === null || $args[$key] === '') {
                continue;
            }
            $value = $args[$key];
            if (in_array($key, $switches, true)) {
                if ($value === true) {
                    $out[$key] = true;
                }
                continue;
            }
            if (is_int($value) || is_float($value)) {
                $out[$key] = $value;
                continue;
            }
            $limit = isset($limits[$key]) ? $limits[$key] : self::MAX_VALUE;
            if (!is_string($value) || mb_strlen($value) > $limit) {
                throw new BridgeError(422, 'invalid_argument', $key . ' must be text of at most ' . $limit . ' characters');
            }
            $out[$key] = $value;
        }
        foreach ($required as $key) {
            if (!isset($out[$key])) {
                throw new BridgeError(422, 'invalid_argument', $what . ' needs ' . $key);
            }
        }
        return $out;
    }

    protected static function ok(array $out): array
    {
        return ['content' => [['type' => 'text', 'text' => json_encode($out, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE)]],
                'structuredContent' => $out, 'isError' => false];
    }

    protected static function error(string $text): array
    {
        return ['content' => [['type' => 'text', 'text' => $text]], 'isError' => true];
    }

    protected static function schema(array $props, array $required): array
    {
        return ['type' => 'object', 'additionalProperties' => false, 'properties' => $props ?: new \stdClass(),
                'required' => $required];
    }
}
