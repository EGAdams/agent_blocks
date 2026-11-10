<?php
declare(strict_types=1);

namespace AgentBridge;

/** What a client may do. Shared by the REST routes and the MCP tools. */
final class ClientTasks
{
    /** @var SubmitWorkflow */  private $submit;
    /** @var ITaskRepository */ private $tasks;
    /** @var IAgentRegistry */  private $agents;
    /** @var IAuditLogger */    private $audit;

    public function __construct(SubmitWorkflow $submit, ITaskRepository $tasks, IAgentRegistry $agents, IAuditLogger $audit)
    {
        $this->submit = $submit;
        $this->tasks  = $tasks;
        $this->agents = $agents;
        $this->audit  = $audit;
    }

    /** @return array [status, view] */
    public function submit(Request $request): array
    {
        $context = $this->submit->run($request);
        return [$context->replayed ? 200 : 201, self::view($context->task) + ['replayed' => $context->replayed]];
    }

    public function status(Principal $who, string $id): array
    {
        return self::view($this->owned($who, $id)) + [
            'recent_events' => $this->events($who, $id, -1)['events'],
        ];
    }

    public function events(Principal $who, string $id, int $after): array
    {
        $this->owned($who, $id);
        return ['task_id' => $id, 'events' => array_map(function ($e) {
            return ['seq' => (int) $e['seq'], 'kind' => $e['kind'], 'message' => $e['message'], 'at' => (int) $e['created_at']];
        }, $this->tasks->events($id, $after))];
    }

    public function result(Principal $who, string $id): array
    {
        $task   = $this->owned($who, $id);
        $result = $this->tasks->result($id);
        $about  = ['operation' => $task['operation'], 'workspace' => $task['workspace'],
                   'approval_mode' => $task['approval_mode']];
        if ($result === null) {
            return ['task_id' => $id, 'state' => $task['state'], 'ready' => false,
                    'last_error' => $task['last_error']] + $about;
        }
        $details = isset($result['details']) ? json_decode((string) $result['details'], true) : null;
        return ['task_id' => $id, 'state' => $task['state'], 'ready' => true, 'ok' => (bool) $result['ok'],
                'summary' => $result['summary'], 'body' => $result['body'], 'performer' => $result['performer'],
                'completed_at' => (int) $result['completed_at']]
             + $about
             // an empty details object stays an object on the wire, never []
             + ['details' => is_array($details) && $details ? $details : new \stdClass()];
    }

    public function cancel(Principal $who, string $id): array
    {
        $this->owned($who, $id);
        $task = $this->tasks->requestCancel($id);
        $this->audit->record($who->id, 'cancel', $id, $task['state']);
        return self::view($task);
    }

    public function agents(): array
    {
        return ['agents' => $this->agents->enabledAgents()];
    }

    private function owned(Principal $who, string $id): array
    {
        if (!preg_match('/^t_[0-9a-f]{24}$/', $id)) {
            throw new BridgeError(404, 'not_found', 'no such task');
        }
        $task = $this->tasks->find($id);
        if ($task === null || $task['client_id'] !== $who->id) {
            throw new BridgeError(404, 'not_found', 'no such task');
        }
        return $task;
    }

    /** The client's view of a task. The prompt is deliberately left out. */
    public static function view(array $task): array
    {
        return [
            'task_id'          => $task['id'],
            'state'            => $task['state'],
            'agent'            => $task['agent'],
            'workspace'        => $task['workspace'],
            'operation'        => $task['operation'],
            'requested_mode'   => $task['requested_mode'],
            'approval_mode'    => $task['approval_mode'],
            'attempts'         => (int) $task['attempts'],
            'cancel_requested' => (int) $task['cancel_requested'] === 1,
            'last_error'       => $task['last_error'],
            'created_at'       => (int) $task['created_at'],
            'updated_at'       => (int) $task['updated_at'],
        ];
    }
}

/** What a worker may do. */
final class WorkerTasks
{
    const EVENT_KINDS = ['progress', 'log', 'phase'];

    /** @var ITaskLeaseManager */ private $leases;
    /** @var ITaskRepository */   private $tasks;
    /** @var IAuditLogger */      private $audit;
    /** @var int */               private $leaseSeconds;
    /** @var int */               private $maxDetails;
    private $clientScopes;

    public function __construct(ITaskLeaseManager $leases, ITaskRepository $tasks, IAuditLogger $audit, int $leaseSeconds,
                                int $maxDetails = 131072, ?ClientScopes $clientScopes = null)
    {
        $this->leases       = $leases;
        $this->tasks        = $tasks;
        $this->audit        = $audit;
        $this->leaseSeconds = $leaseSeconds;
        $this->maxDetails   = $maxDetails;
        $this->clientScopes = $clientScopes ?: new ClientScopes();
    }

    /** @param string[] $skip `operation@workspace` pairs this worker's lane does not run */
    public function next(array $skip = []): ?array
    {
        $this->leases->recoverExpired();
        $task = $this->leases->next($skip);
        return $task === null ? null : ['task_id' => $task['id']];
    }

    public function claim(Principal $who, string $id, array $body): array
    {
        $this->leases->recoverExpired();
        $workerId = self::text($body, 'worker_id', '/^[A-Za-z0-9._-]{1,64}$/');
        $task     = $this->leases->claim($id, $workerId, $this->leaseSeconds);
        if ($task === null) {
            $this->audit->record($who->id, 'claim', $id, 'lost');
            throw new BridgeError(409, 'not_claimable', 'the task is not queued, or another worker claimed it');
        }
        $this->audit->record($who->id, 'claim', $id, 'claimed', ['worker_id' => $workerId, 'attempt' => (int) $task['attempts']]);
        // The worker is the one party that gets the prompt and the lease token.
        return [
            'task_id'          => $task['id'],
            'lease_token'      => $task['lease_token'],
            'sender'           => $this->clientScopes->sender($task['client_id']),
            'lease_expires_at' => $task['lease_expires_at'],
            'lease_seconds'    => $this->leaseSeconds,
            'attempt'          => (int) $task['attempts'],
            'agent'            => $task['agent'],
            'workspace'        => $task['workspace'],
            'operation'        => $task['operation'],
            'approval_mode'    => $task['approval_mode'],
            'prompt'           => $task['prompt'],
        ];
    }

    public function heartbeat(string $id, array $body): array
    {
        $beat = $this->leases->heartbeat($id, self::lease($body), $this->leaseSeconds);
        if ($beat === null) {
            throw new BridgeError(409, 'lease_invalid', 'the lease is expired or not yours');
        }
        return ['task_id' => $id] + $beat;
    }

    public function event(string $id, array $body): array
    {
        $lease = self::lease($body);
        $kind  = self::text($body, 'kind', '/^[a-z]{1,20}$/');
        if (!in_array($kind, self::EVENT_KINDS, true)) {
            throw new BridgeError(422, 'invalid_event', 'kind must be one of: ' . implode(', ', self::EVENT_KINDS));
        }
        $message = self::clip(isset($body['message']) && is_string($body['message']) ? $body['message'] : '', 2000);
        if ($this->leases->heartbeat($id, $lease, $this->leaseSeconds) === null) {
            throw new BridgeError(409, 'lease_invalid', 'the lease is expired or not yours');
        }
        $this->tasks->appendEvent($id, $kind, $message);
        return ['task_id' => $id, 'recorded' => true];
    }

    public function complete(Principal $who, string $id, array $body): array
    {
        $result = [
            'ok'        => !empty($body['ok']),
            'summary'   => self::clip(isset($body['summary']) && is_string($body['summary']) ? $body['summary'] : '', 2000),
            'body'      => self::clip(isset($body['body']) && is_string($body['body']) ? $body['body'] : '', 65536),
            'performer' => self::clip(isset($body['performer']) && is_string($body['performer']) ? $body['performer'] : '', 100),
            'details'   => $this->details($body),
        ];
        if (!$this->leases->complete($id, self::lease($body), $result)) {
            throw new BridgeError(409, 'lease_invalid', 'the lease is expired or not yours');
        }
        $this->audit->record($who->id, 'complete', $id, $result['ok'] ? 'succeeded' : 'failed', ['bytes' => strlen($result['body'])]);
        return ['task_id' => $id, 'state' => $result['ok'] ? TaskState::SUCCEEDED : TaskState::FAILED];
    }

    public function fail(Principal $who, string $id, array $body): array
    {
        $reason = self::clip(isset($body['reason']) && is_string($body['reason']) ? $body['reason'] : 'unspecified', 500);
        $state  = $this->leases->fail($id, self::lease($body), $reason, !empty($body['retryable']), !empty($body['cancelled']));
        if ($state === null) {
            throw new BridgeError(409, 'lease_invalid', 'the lease is expired or not yours');
        }
        $this->audit->record($who->id, 'fail', $id, $state, ['retryable' => !empty($body['retryable'])]);
        return ['task_id' => $id, 'state' => $state];
    }

    /**
     * The structured report (changed paths, diff, checks, artifacts) as stored JSON.
     * Never clipped: a report too big to keep is withheld whole and says so, so a
     * half file is never handed back as if it were the file.
     */
    private function details(array $body): string
    {
        if (!isset($body['details']) || !is_array($body['details']) || !$body['details']) {
            return '{}';
        }
        $json = json_encode($body['details'], JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
        if ($json === false || substr($json, 0, 1) !== '{') {
            return '{}';
        }
        if (strlen($json) > $this->maxDetails) {
            return json_encode(['details_withheld' => true, 'complete' => false,
                                'reason' => 'the report was ' . strlen($json) . ' bytes; the limit is ' . $this->maxDetails
                                          . '. The full report is on the worker machine.']);
        }
        return $json;
    }

    private static function lease(array $body): string
    {
        return self::text($body, 'lease_token', '/^[A-Za-z0-9_-]{20,200}$/');
    }

    private static function text(array $body, string $key, string $pattern): string
    {
        if (!isset($body[$key]) || !is_string($body[$key]) || !preg_match($pattern, $body[$key])) {
            throw new BridgeError(422, 'invalid_field', $key . ' is missing or malformed');
        }
        return $body[$key];
    }

    private static function clip(string $text, int $max): string
    {
        if (strlen($text) <= $max) {
            return $text;
        }
        return function_exists('mb_strcut') ? mb_strcut($text, 0, $max, 'UTF-8') : substr($text, 0, $max);
    }
}

/** The router. Turns a Request into a Response and nothing else. */
final class GatewayApp
{
    /** @var ITaskAuthenticator */ private $auth;
    /** @var ClientTasks */        private $client;
    /** @var WorkerTasks */        private $worker;
    /** @var McpEndpoint */        private $mcp;
    /** @var OAuthServer */        private $oauth;
    /** @var int */                private $maxBody;
    /** @var int the worker's complete report only; every other request keeps maxBody */ private $maxResultBody;
    /** @var array<string,string> the WWW-Authenticate header for a 401 */ private $challenge;
    /** @var WorkerScans|null the worker's scan uploads; absent means no scan routes */ private $scans;
    /** @var int one uploaded page or original only */ private $maxBlob;
    /** @var WorkerDownloads|null the worker's download uploads; absent means no download routes */ private $downloads;
    /** @var WorkerUploads|null the Notes worker's fetch of a client's PDF upload; absent means no such route */ private $uploads;
    /** @var int an MCP request only: a notes_upload_chunk carries up to 256 KiB of base64 */ private $maxMcpBody;

    /** @var AlertWatch|null the gateway's own queue watch, refreshed on any request */ private $watch;
    /** @var AlertViewers|null who may read and acknowledge alerts */ private $viewers;

    const BLOB_ROUTE = '#^/worker/scans/(scan_[0-9A-HJKMNP-TV-Z]{26})/blobs/([0-9a-f]{64})$#';
    const SCAN_ROUTE = '#^/worker/scans/(scan_[0-9A-HJKMNP-TV-Z]{26})$#';
    const DOWNLOAD_BLOB_ROUTE = '#^/worker/downloads/(t_[0-9a-f]{24})/blobs/([0-9a-f]{64})$#';
    const DOWNLOAD_ROUTE      = '#^/worker/downloads/(t_[0-9a-f]{24})$#';
    const UPLOAD_ROUTE        = '#^/worker/tasks/(t_[0-9a-f]{24})/upload$#';

    public function __construct(ITaskAuthenticator $auth, ClientTasks $client, WorkerTasks $worker, McpEndpoint $mcp,
                                OAuthServer $oauth, int $maxBody, array $challenge, int $maxResultBody = 0,
                                ?WorkerScans $scans = null, int $maxBlob = 0, ?WorkerDownloads $downloads = null,
                                ?WorkerUploads $uploads = null, int $maxMcpBody = 0, ?AlertWatch $watch = null,
                                ?AlertViewers $viewers = null)
    {
        $this->watch         = $watch;
        $this->viewers       = $viewers;
        $this->uploads       = $uploads;
        $this->maxMcpBody    = max($maxBody, $maxMcpBody);
        $this->downloads     = $downloads;
        $this->maxResultBody = max($maxBody, $maxResultBody);
        $this->scans         = $scans;
        $this->maxBlob       = max($maxBody, $maxBlob);
        $this->auth      = $auth;
        $this->client    = $client;
        $this->worker    = $worker;
        $this->mcp       = $mcp;
        $this->oauth     = $oauth;
        $this->maxBody   = $maxBody;
        $this->challenge = $challenge;
    }

    public function handle(Request $request): Response
    {
        if ($this->watch !== null) {
            $this->watch->tick();
        }
        try {
            return $this->route($request);
        } catch (BridgeError $e) {
            $headers = $e->status === 401 ? $this->challenge : [];
            return Response::error($e->status, $e->errorCode, $e->getMessage(), $headers);
        } catch (\Throwable $e) {
            error_log('agent-bridge: ' . get_class($e) . ' at ' . $e->getFile() . ':' . $e->getLine());
            return Response::error(500, 'internal', 'internal error');
        }
    }

    private function route(Request $r): Response
    {
        $m = $r->method;
        $p = rtrim($r->path, '/');
        $isReport = (bool) preg_match('#^/worker/tasks/t_[0-9a-f]{24}/complete$#', $p) || (bool) preg_match(self::SCAN_ROUTE, $p)
                 || (bool) preg_match(self::DOWNLOAD_ROUTE, $p);
        $isBlob   = (bool) preg_match(self::BLOB_ROUTE, $p) || (bool) preg_match(self::DOWNLOAD_BLOB_ROUTE, $p);
        $limit    = $isBlob ? $this->maxBlob : ($isReport ? $this->maxResultBody : ($p === '/mcp' ? $this->maxMcpBody : $this->maxBody));
        if (strlen($r->body) > $limit) {
            throw new BridgeError(413, 'too_large', 'request body too large');
        }
        if ($p === '' || $p === '/health') {
            return new Response(200, ['ok' => true, 'service' => 'agent-bridge', 'version' => '1']);
        }
        if ($this->oauth->serves($p)) {
            return $this->oauth->handle($r);
        }
        if ($p === '/mcp') {
            return $this->mcp->handle($r);
        }
        if ($m === 'POST' && $p === '/tasks') {
            list($status, $view) = $this->client->submit($r);
            return new Response($status, $view);
        }
        if ($m === 'GET' && $p === '/agents') {
            $this->principal($r, 'client');
            return new Response(200, $this->client->agents());
        }
        if (preg_match('#^/tasks/([^/]+)(/events|/result|/cancel)?$#', $p, $g)) {
            $who  = $this->principal($r, 'client');
            $tail = isset($g[2]) ? $g[2] : '';
            if ($m === 'GET' && $tail === '') {
                return new Response(200, $this->client->status($who, $g[1]));
            }
            if ($m === 'GET' && $tail === '/events') {
                $after = isset($r->query['after']) ? max(0, (int) $r->query['after']) : 0;
                return new Response(200, $this->client->events($who, $g[1], $after));
            }
            if ($m === 'GET' && $tail === '/result') {
                return new Response(200, $this->client->result($who, $g[1]));
            }
            if ($m === 'POST' && $tail === '/cancel') {
                return new Response(200, $this->client->cancel($who, $g[1]));
            }
            throw new BridgeError(405, 'method_not_allowed', 'method not allowed');
        }
        if ($m === 'GET' && $p === '/worker/tasks/next') {
            $who  = $this->principal($r, 'worker');
            $skip = $this->laneSkips($r);
            if ($this->watch !== null) {
                $this->watch->workerSeen($who, $skip);
            }
            $next = $this->worker->next($skip);
            return $next === null ? new Response(204, null) : new Response(200, $next);
        }
        if ($this->watch !== null && $m === 'GET' && $p === '/alerts') {
            $who = $this->auth->authenticate($r);
            if ($who === null) {
                throw new BridgeError(401, 'unauthenticated', 'a valid bearer token is required');
            }
            if ($who->role !== 'worker' && ($this->viewers === null || !$this->viewers->allows($who))) {
                throw new BridgeError(403, 'not_allowed', 'this token may not read Bridge alerts');
            }
            $this->watch->refresh();
            return new Response(200, $this->watch->snapshot());
        }
        if ($this->watch !== null && $this->watch->requests() !== null && preg_match('#^/requests(?:/([^/]+)(?:/(update|close))?)?$#', $p, $g)) {
            return $this->requestRoute($r, $m, $g);
        }
        if ($this->watch !== null && preg_match('#^/alerts/([^/]+)/ack$#', $p, $g)) {
            if ($m !== 'POST') {
                throw new BridgeError(405, 'method_not_allowed', 'method not allowed');
            }
            $who = $this->principal($r, 'client');
            if ($this->viewers === null || !$this->viewers->allows($who)) {
                throw new BridgeError(403, 'not_allowed', 'this token may not acknowledge Bridge alerts');
            }
            $body = $this->json($r);
            return new Response(200, $this->watch->acknowledge($who, $g[1], isset($body['note']) && is_string($body['note']) ? $body['note'] : ''));
        }
        if ($this->uploads !== null && preg_match(self::UPLOAD_ROUTE, $p, $g)) {
            if ($m !== 'POST') {               // POST: the lease token travels in the body, never in a logged URL
                throw new BridgeError(405, 'method_not_allowed', 'method not allowed');
            }
            $who = $this->principal($r, 'worker');
            return new Response(200, $this->uploads->fetch($who, $g[1], (string) ($this->json($r)['lease_token'] ?? '')));
        }
        if (preg_match('#^/worker/tasks/(t_[0-9a-f]{24})/(claim|heartbeat|events|complete|fail)$#', $p, $g)) {
            if ($m !== 'POST') {
                throw new BridgeError(405, 'method_not_allowed', 'method not allowed');
            }
            $who  = $this->principal($r, 'worker');
            $body = $this->json($r);
            switch ($g[2]) {
                case 'claim':     return new Response(200, $this->worker->claim($who, $g[1], $body));
                case 'heartbeat': return new Response(200, $this->worker->heartbeat($g[1], $body));
                case 'events':    return new Response(200, $this->worker->event($g[1], $body));
                case 'complete':  return new Response(200, $this->worker->complete($who, $g[1], $body));
                default:          return new Response(200, $this->worker->fail($who, $g[1], $body));
            }
        }
        if ($this->scans !== null && ($isBlob || $isReport) && preg_match('#^/worker/scans/#', $p)) {
            if ($m !== 'POST') {
                throw new BridgeError(405, 'method_not_allowed', 'method not allowed');
            }
            $who = $this->principal($r, 'worker');
            if ($isBlob) {
                preg_match(self::BLOB_ROUTE, $p, $g);
                return new Response(200, $this->scans->blob($who, $g[1], $g[2], $r->body));
            }
            preg_match(self::SCAN_ROUTE, $p, $g);
            return new Response(200, $this->scans->manifest($who, $g[1], $this->json($r)));
        }
        if ($this->downloads !== null && ($isBlob || $isReport) && preg_match('#^/worker/downloads/#', $p)) {
            if ($m !== 'POST') {
                throw new BridgeError(405, 'method_not_allowed', 'method not allowed');
            }
            $who = $this->principal($r, 'worker');
            if (preg_match(self::DOWNLOAD_BLOB_ROUTE, $p, $g)) {
                return new Response(200, $this->downloads->blob($who, $g[1], $g[2], $r->body));
            }
            preg_match(self::DOWNLOAD_ROUTE, $p, $g);
            return new Response(200, $this->downloads->manifest($who, $g[1], $this->json($r)));
        }
        throw new BridgeError(404, 'not_found', 'no such route');
    }

    /** `?skip=implement@agent_blocks_dev,implement@letta_code`: a worker lane's pass-over list, validated. */
    /** Supervised requests over REST, for clients that do not speak MCP (Toyota): the same calls as the request_* tools. */
    private function requestRoute(Request $r, string $m, array $g): Response
    {
        $who = $this->principal($r, 'client');
        if ($this->viewers === null || !$this->viewers->allows($who)) {
            throw new BridgeError(403, 'not_allowed', 'this token may not open supervised requests');
        }
        $requests = $this->watch->requests();
        $id       = $g[1] ?? '';
        $action   = $g[2] ?? '';
        if ($id === '') {
            if ($m === 'GET') {
                return new Response(200, $requests->listing($who, ($r->query['include_closed'] ?? '') === '1', true));
            }
            if ($m === 'POST') {
                return new Response(201, $requests->open($who, $this->json($r)));
            }
        } elseif ($action === '' && $m === 'GET') {
            return new Response(200, $requests->status($who, ['request_id' => $id], true));
        } elseif ($m === 'POST') {
            $body = ['request_id' => $id] + $this->json($r);
            return new Response(200, $action === 'update' ? $requests->update($who, $body) : $requests->close($who, $body));
        }
        throw new BridgeError(405, 'method_not_allowed', 'method not allowed');
    }

    private function laneSkips(Request $r): array
    {
        $raw = isset($r->query['skip']) ? (string) $r->query['skip'] : '';
        if ($raw === '') {
            return [];
        }
        $pairs = explode(',', $raw);
        if (count($pairs) > 32) {
            throw new BridgeError(400, 'bad_skip', 'too many skip entries');
        }
        foreach ($pairs as $pair) {
            if (!preg_match('/^[a-z_]{1,40}@[A-Za-z0-9._-]{1,64}$/', $pair)) {
                throw new BridgeError(400, 'bad_skip', 'skip entries look like operation@workspace');
            }
        }
        return $pairs;
    }

    private function principal(Request $r, string $role): Principal
    {
        $who = $this->auth->authenticate($r);
        if ($who === null) {
            throw new BridgeError(401, 'unauthenticated', 'a valid bearer token is required');
        }
        if ($who->role !== $role) {
            throw new BridgeError(403, 'wrong_role', 'this token may not use that endpoint');
        }
        return $who;
    }

    private function json(Request $r): array
    {
        $body = $r->body === '' ? [] : json_decode($r->body, true);
        if (!is_array($body)) {
            throw new BridgeError(400, 'bad_json', 'body must be a JSON object');
        }
        return $body;
    }
}
