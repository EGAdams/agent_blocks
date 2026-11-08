<?php
declare(strict_types=1);

// Plain-PHP test runner: php tests/run_tests.php
// In-memory SQLite, a fake clock and predictable ids. No network, no web server.

namespace AgentBridge\Tests;

use AgentBridge\Bootstrap;
use AgentBridge\Database;
use AgentBridge\IClock;
use AgentBridge\IIdGenerator;
use AgentBridge\OAuthStore;
use AgentBridge\Request;
use AgentBridge\Response;
use AgentBridge\TokenAuthenticator;
use PDO;

require_once __DIR__ . '/../src/Bootstrap.php';

final class FakeClock implements IClock
{
    public $t = 1000000;
    public function now(): int { return $this->t; }
}

final class FakeIds implements IIdGenerator
{
    private $n = 0;
    public function newId(string $prefix): string
    {
        $this->n++;
        return $prefix . str_pad(dechex($this->n), 24, '0', STR_PAD_LEFT);
    }
    public function newSecret(): string
    {
        $this->n++;
        return 'secret_' . str_pad((string) $this->n, 30, 'x');
    }
}

final class World
{
    public $pdo; public $clock; public $app; public $client; public $client2; public $worker;

    public function __construct(array $overrides = [])
    {
        $config = $overrides + [
            'database' => ':memory:', 'lease_seconds' => 60, 'max_attempts' => 2, 'max_body' => 65536,
            'allowed_operations' => ['inspect', 'explain', 'search_memory'],
            'allowed_modes' => ['read_only'],
            'memory_agent' => 'codex', 'memory_workspace' => 'agent_blocks',
            'agents' => [
                ['name' => 'codex', 'display_name' => 'Codex', 'description' => 'reads code',
                 'operations' => ['inspect', 'explain', 'search_memory'], 'workspaces' => ['agent_blocks']],
                ['name' => 'hello-world', 'display_name' => 'Hello', 'description' => 'echo',
                 'operations' => ['inspect'], 'workspaces' => ['agent_blocks']],
            ],
        ];
        $this->pdo   = Database::open(':memory:');
        $this->clock = new FakeClock();
        $ids         = new FakeIds();
        Bootstrap::migrate($this->pdo, $config, __DIR__ . '/../migrations');
        $this->app     = Bootstrap::build($config, $this->pdo, $this->clock, $ids);
        $this->client  = TokenAuthenticator::issue($this->pdo, $ids, $this->clock, 'client', 'test client')['token'];
        $this->client2 = TokenAuthenticator::issue($this->pdo, $ids, $this->clock, 'client', 'other client')['token'];
        $this->worker  = TokenAuthenticator::issue($this->pdo, $ids, $this->clock, 'worker', 'test worker')['token'];
    }

    public function call(string $method, string $path, ?string $token, $body = null, array $query = []): Response
    {
        $headers = $token === null ? [] : ['Authorization' => 'Bearer ' . $token];
        $raw     = $body === null ? '' : (is_string($body) ? $body : json_encode($body));
        return $this->app->handle(new Request($method, $path, $headers, $raw, $query));
    }

    public function submit(array $extra = [], ?string $token = null): Response
    {
        return $this->call('POST', '/tasks', $token ?: $this->client, $extra + envelope());
    }

    /** Submits, claims, and returns [task_id, lease_token]. */
    public function running(): array
    {
        $id    = $this->submit()->json['task_id'];
        $claim = $this->call('POST', "/worker/tasks/$id/claim", $this->worker, ['worker_id' => 'w1']);
        return [$id, $claim->json['lease_token']];
    }

    public function mcp(string $method, array $params = [], $id = 1, ?string $token = null): Response
    {
        $msg = ['jsonrpc' => '2.0', 'method' => $method, 'params' => $params];
        if ($id !== null) {
            $msg['id'] = $id;
        }
        return $this->call('POST', '/mcp', $token ?: $this->client, $msg);
    }
}

function envelope(): array
{
    return ['schema_version' => '1.0', 'task_type' => 'agent_task', 'agent' => 'codex', 'workspace' => 'agent_blocks',
            'operation' => 'explain', 'approval_mode' => 'read_only',
            'prompt' => 'Inspect the Toyota voice system and explain its architecture without changing any files.'];
}

$tests = [];
$fails = 0;
function test(string $name, callable $body) { global $tests; $tests[$name] = $body; }
function eq($expected, $actual, string $what = '')
{
    if ($expected !== $actual) {
        throw new \RuntimeException(($what ? "$what: " : '') . 'expected ' . var_export($expected, true) . ', got ' . var_export($actual, true));
    }
}
function ok($cond, string $what) { if (!$cond) { throw new \RuntimeException($what); } }

// ---- auth and roles ------------------------------------------------------

test('health needs no token and says nothing private', function () {
    $w = new World();
    $r = $w->call('GET', '/health', null);
    eq(200, $r->status);
    eq(['ok' => true, 'service' => 'agent-bridge', 'version' => '1'], $r->json);
});

test('submit without a token is 401 with WWW-Authenticate', function () {
    $w = new World();
    $r = $w->call('POST', '/tasks', null, envelope());
    eq(401, $r->status);
    ok(isset($r->headers['WWW-Authenticate']), 'missing WWW-Authenticate');
});

test('a bad token is 401', function () {
    $w = new World();
    eq(401, $w->call('POST', '/tasks', 'not_a_real_token_at_all_123', envelope())->status);
});

test('a revoked token is 401', function () {
    $w = new World();
    $w->pdo->exec("UPDATE api_tokens SET revoked = 1 WHERE label = 'test client'");
    eq(401, $w->submit()->status);
});

test('a worker token cannot submit; a client token cannot claim', function () {
    $w = new World();
    eq(403, $w->submit([], $w->worker)->status);
    $id = $w->submit()->json['task_id'];
    eq(403, $w->call('POST', "/worker/tasks/$id/claim", $w->client, ['worker_id' => 'w1'])->status);
    eq(403, $w->call('GET', '/worker/tasks/next', $w->client)->status);
});

test('a client cannot see another client\'s task', function () {
    $w  = new World();
    $id = $w->submit()->json['task_id'];
    eq(404, $w->call('GET', "/tasks/$id", $w->client2)->status);
    eq(404, $w->call('GET', "/tasks/$id/result", $w->client2)->status);
    eq(404, $w->call('POST', "/tasks/$id/cancel", $w->client2)->status);
});

// ---- validation and policy -----------------------------------------------

test('a valid read-only task is queued', function () {
    $w = new World();
    $r = $w->submit();
    eq(201, $r->status);
    eq('queued', $r->json['state']);
    eq('read_only', $r->json['approval_mode']);
    ok(!array_key_exists('prompt', $r->json), 'the client view must not echo the prompt');
});

test('unknown fields are rejected', function () {
    $w = new World();
    $r = $w->submit(['command' => 'rm -rf /']);
    eq(422, $r->status);
    ok(strpos($r->json['error']['message'], 'unknown field: command') !== false, 'no unknown-field message');
});

test('missing and malformed fields are rejected', function () {
    $w   = new World();
    $env = envelope();
    unset($env['prompt']);
    eq(422, $w->call('POST', '/tasks', $w->client, $env)->status);
    eq(422, $w->submit(['workspace' => '../etc'])->status);
    eq(422, $w->submit(['workspace' => '/home/adamsl'])->status);
    eq(422, $w->submit(['agent' => 'Codex; ls'])->status);
    eq(422, $w->submit(['prompt' => '   '])->status);
    eq(422, $w->submit(['prompt' => str_repeat('a', 8001)])->status);
    eq(422, $w->submit(['schema_version' => '2.0'])->status);
    eq(422, $w->submit(['operation' => 'shell'])->status);
    eq(422, $w->submit(['idempotency_key' => 'has space'])->status);
});

test('a non-object body is 400', function () {
    $w = new World();
    eq(400, $w->call('POST', '/tasks', $w->client, '[1,2]')->status);
    eq(400, $w->call('POST', '/tasks', $w->client, 'not json')->status);
});

test('an oversized body is 413', function () {
    $w = new World(['max_body' => 100]);
    eq(413, $w->submit()->status);
});

test('modify_code is refused by policy even though it is a valid shape', function () {
    $w = new World();
    $r = $w->submit(['operation' => 'modify_code']);
    eq(403, $r->status);
    eq('not_allowed', $r->json['error']['code']);
});

test('approved_change is refused: the client cannot grant itself write access', function () {
    $w = new World();
    eq(403, $w->submit(['approval_mode' => 'approved_change'])->status);
    eq(403, $w->submit(['approval_mode' => 'propose'])->status);
});

test('an unknown agent, a wrong operation for the agent, or a wrong workspace is refused', function () {
    $w = new World();
    eq(403, $w->submit(['agent' => 'nobody'])->status);
    eq(403, $w->submit(['agent' => 'hello-world', 'operation' => 'explain'])->status);
    eq(403, $w->submit(['workspace' => 'rol_finances'])->status);
});

test('a refusal is audited without the prompt', function () {
    $w = new World();
    $w->submit(['operation' => 'modify_code', 'prompt' => 'SECRET-PROMPT-TEXT']);
    $rows = $w->pdo->query("SELECT * FROM audit_events WHERE outcome = 'refused'")->fetchAll();
    eq(1, count($rows));
    ok(strpos(json_encode($rows), 'SECRET-PROMPT-TEXT') === false, 'the prompt leaked into the audit log');
});

test('the audit log never holds a prompt or a token', function () {
    $w = new World();
    list($id, $lease) = $w->running();
    $w->call('POST', "/worker/tasks/$id/complete", $w->worker, ['lease_token' => $lease, 'ok' => true, 'summary' => 's', 'body' => 'b']);
    $dump = json_encode($w->pdo->query('SELECT * FROM audit_events')->fetchAll());
    ok(strpos($dump, 'Toyota voice') === false, 'prompt in audit');
    ok(strpos($dump, $lease) === false, 'lease token in audit');
    ok(strpos($dump, $w->client) === false, 'client token in audit');
});

test('tokens are stored only as hashes', function () {
    $w    = new World();
    $dump = json_encode($w->pdo->query('SELECT * FROM api_tokens')->fetchAll());
    ok(strpos($dump, $w->client) === false && strpos($dump, $w->worker) === false, 'a raw token is stored');
});

// ---- idempotency ---------------------------------------------------------

test('the same idempotency key replays the same task', function () {
    $w = new World();
    $a = $w->submit(['idempotency_key' => 'k-1']);
    $b = $w->submit(['idempotency_key' => 'k-1']);
    eq(201, $a->status);
    eq(200, $b->status);
    eq($a->json['task_id'], $b->json['task_id']);
    eq(true, $b->json['replayed']);
    eq(1, (int) $w->pdo->query('SELECT COUNT(*) FROM tasks')->fetchColumn());
});

test('the same key with a different prompt is a 409 conflict', function () {
    $w = new World();
    $w->submit(['idempotency_key' => 'k-1']);
    $r = $w->submit(['idempotency_key' => 'k-1', 'prompt' => 'something else']);
    eq(409, $r->status);
    eq('idempotency_conflict', $r->json['error']['code']);
});

test('idempotency keys are per client', function () {
    $w = new World();
    $a = $w->submit(['idempotency_key' => 'k-1']);
    $b = $w->submit(['idempotency_key' => 'k-1'], $w->client2);
    eq(201, $b->status);
    ok($a->json['task_id'] !== $b->json['task_id'], 'two clients shared a task');
});

// ---- claims and leases ---------------------------------------------------

test('next returns 204 when the queue is empty, then the oldest task', function () {
    $w = new World();
    eq(204, $w->call('GET', '/worker/tasks/next', $w->worker)->status);
    $first = $w->submit()->json['task_id'];
    $w->clock->t++;
    $w->submit(['prompt' => 'second']);
    $r = $w->call('GET', '/worker/tasks/next', $w->worker);
    eq(200, $r->status);
    eq($first, $r->json['task_id']);
    ok(!isset($r->json['prompt']), 'next must not hand out the prompt before a claim');
});

test('lane: next?skip passes over matching tasks, keeps order, and changes nothing', function () {
    $w     = new World();
    $long  = $w->submit()->json['task_id'];                 // explain@agent_blocks, oldest
    $w->clock->t++;
    $quick = $w->submit(['agent' => 'hello-world', 'operation' => 'inspect'])->json['task_id'];
    $w->clock->t++;
    $later = $w->submit(['agent' => 'hello-world', 'operation' => 'inspect', 'prompt' => 'another quick one please'])->json['task_id'];
    eq($long,  $w->call('GET', '/worker/tasks/next', $w->worker)->json['task_id'], 'no skip: oldest first, as before');
    $lane = $w->call('GET', '/worker/tasks/next', $w->worker, null, ['skip' => 'explain@agent_blocks']);
    eq($quick, $lane->json['task_id'], 'the skipped task is passed over, the next oldest is offered');
    eq($long,  $w->call('GET', '/worker/tasks/next', $w->worker)->json['task_id'], 'the skipped task is still queued');
    $w->call('POST', "/worker/tasks/$quick/claim", $w->worker, ['worker_id' => 'express']);
    eq($later, $w->call('GET', '/worker/tasks/next', $w->worker, null, ['skip' => 'explain@agent_blocks'])->json['task_id']);
    eq(204, $w->call('GET', '/worker/tasks/next', $w->worker, null,
                     ['skip' => 'explain@agent_blocks,inspect@agent_blocks'])->status, 'everything skipped is an empty lane');
});

test('lane: a malformed skip list is refused, and a client token still cannot use the route', function () {
    $w = new World();
    foreach (['explain', 'explain@', "explain@x'; DROP TABLE tasks;--", '@ws', str_repeat('a@b,', 40)] as $bad) {
        eq(400, $w->call('GET', '/worker/tasks/next', $w->worker, null, ['skip' => $bad])->status, $bad);
    }
    eq(403, $w->call('GET', '/worker/tasks/next', $w->client, null, ['skip' => 'explain@agent_blocks'])->status);
});

test('a claim hands the worker the prompt and a lease; a second claim loses', function () {
    $w  = new World();
    $id = $w->submit()->json['task_id'];
    $a  = $w->call('POST', "/worker/tasks/$id/claim", $w->worker, ['worker_id' => 'w1']);
    eq(200, $a->status);
    ok(strlen($a->json['lease_token']) >= 20, 'no lease token');
    eq(envelope()['prompt'], $a->json['prompt']);
    eq(1, $a->json['attempt']);
    $b = $w->call('POST', "/worker/tasks/$id/claim", $w->worker, ['worker_id' => 'w2']);
    eq(409, $b->status);
    eq('running', $w->call('GET', "/tasks/$id", $w->client)->json['state']);
});

test('a malformed worker_id is rejected', function () {
    $w  = new World();
    $id = $w->submit()->json['task_id'];
    eq(422, $w->call('POST', "/worker/tasks/$id/claim", $w->worker, ['worker_id' => 'w 1; rm'])->status);
});

test('heartbeat extends the lease; a wrong lease token is 409', function () {
    $w = new World();
    list($id, $lease) = $w->running();
    $w->clock->t += 50;
    $r = $w->call('POST', "/worker/tasks/$id/heartbeat", $w->worker, ['lease_token' => $lease]);
    eq(200, $r->status);
    eq($w->clock->t + 60, $r->json['lease_expires_at']);
    eq(false, $r->json['cancel_requested']);
    $bad = $w->call('POST', "/worker/tasks/$id/heartbeat", $w->worker, ['lease_token' => str_repeat('z', 40)]);
    eq(409, $bad->status);
    $w->clock->t += 50;   // still inside the extended lease
    eq(0, $w->pdo->query("SELECT COUNT(*) FROM tasks WHERE state = 'queued'")->fetchColumn() + 0);
});

test('an expired lease goes back to the queue, then fails when attempts run out', function () {
    $w = new World();
    list($id, $lease) = $w->running();
    $w->clock->t += 61;
    eq(200, $w->call('GET', '/worker/tasks/next', $w->worker)->status, 'next should find the requeued task');
    $s = $w->call('GET', "/tasks/$id", $w->client)->json;
    eq('queued', $s['state']);
    eq('lease expired', $s['last_error']);
    // the old lease is dead
    eq(409, $w->call('POST', "/worker/tasks/$id/complete", $w->worker, ['lease_token' => $lease, 'ok' => true])->status);
    // second (last) attempt also expires
    $claim = $w->call('POST', "/worker/tasks/$id/claim", $w->worker, ['worker_id' => 'w2']);
    eq(2, $claim->json['attempt']);
    $w->clock->t += 61;
    $w->call('GET', '/worker/tasks/next', $w->worker);
    eq('failed', $w->call('GET', "/tasks/$id", $w->client)->json['state']);
});

test('a retryable failure is requeued once, then fails', function () {
    $w = new World();
    list($id, $lease) = $w->running();
    $r = $w->call('POST', "/worker/tasks/$id/fail", $w->worker, ['lease_token' => $lease, 'reason' => 'agent timed out', 'retryable' => true]);
    eq('queued', $r->json['state']);
    $claim = $w->call('POST', "/worker/tasks/$id/claim", $w->worker, ['worker_id' => 'w1']);
    $r     = $w->call('POST', "/worker/tasks/$id/fail", $w->worker, ['lease_token' => $claim->json['lease_token'], 'reason' => 'again', 'retryable' => true]);
    eq('failed', $r->json['state']);
});

test('a non-retryable failure fails at once', function () {
    $w = new World();
    list($id, $lease) = $w->running();
    $r = $w->call('POST', "/worker/tasks/$id/fail", $w->worker, ['lease_token' => $lease, 'reason' => 'bad workspace']);
    eq('failed', $r->json['state']);
    $res = $w->call('GET', "/tasks/$id/result", $w->client)->json;
    eq(false, $res['ready']);
    eq('bad workspace', $res['last_error']);
});

// ---- results and terminal states -----------------------------------------

test('complete stores the result and the client reads it', function () {
    $w = new World();
    list($id, $lease) = $w->running();
    eq(false, $w->call('GET', "/tasks/$id/result", $w->client)->json['ready']);
    $r = $w->call('POST', "/worker/tasks/$id/complete", $w->worker,
                  ['lease_token' => $lease, 'ok' => true, 'summary' => 'Toyota is a sidecar', 'body' => 'long answer', 'performer' => 'codex']);
    eq(200, $r->status);
    eq('succeeded', $r->json['state']);
    $res = $w->call('GET', "/tasks/$id/result", $w->client)->json;
    eq(true, $res['ready']);
    eq(true, $res['ok']);
    eq('Toyota is a sidecar', $res['summary']);
    eq('long answer', $res['body']);
    eq('codex', $res['performer']);
});

test('a finished task cannot be completed, failed or claimed again', function () {
    $w = new World();
    list($id, $lease) = $w->running();
    $w->call('POST', "/worker/tasks/$id/complete", $w->worker, ['lease_token' => $lease, 'ok' => true, 'summary' => 'first']);
    eq(409, $w->call('POST', "/worker/tasks/$id/complete", $w->worker, ['lease_token' => $lease, 'ok' => false, 'summary' => 'second'])->status);
    eq(409, $w->call('POST', "/worker/tasks/$id/fail", $w->worker, ['lease_token' => $lease, 'reason' => 'x'])->status);
    eq(409, $w->call('POST', "/worker/tasks/$id/claim", $w->worker, ['worker_id' => 'w1'])->status);
    eq('first', $w->call('GET', "/tasks/$id/result", $w->client)->json['summary']);
    // cancelling a finished task changes nothing
    eq('succeeded', $w->call('POST', "/tasks/$id/cancel", $w->client)->json['state']);
});

// ---- cancel ----------------------------------------------------------------

test('cancelling a queued task cancels it and it is never claimable', function () {
    $w  = new World();
    $id = $w->submit()->json['task_id'];
    eq('cancelled', $w->call('POST', "/tasks/$id/cancel", $w->client)->json['state']);
    eq(204, $w->call('GET', '/worker/tasks/next', $w->worker)->status);
    eq(409, $w->call('POST', "/worker/tasks/$id/claim", $w->worker, ['worker_id' => 'w1'])->status);
});

test('cancelling a running task flags it; the worker sees it and reports cancelled', function () {
    $w = new World();
    list($id, $lease) = $w->running();
    $c = $w->call('POST', "/tasks/$id/cancel", $w->client)->json;
    eq('running', $c['state']);
    eq(true, $c['cancel_requested']);
    $beat = $w->call('POST', "/worker/tasks/$id/heartbeat", $w->worker, ['lease_token' => $lease])->json;
    eq(true, $beat['cancel_requested']);
    $r = $w->call('POST', "/worker/tasks/$id/fail", $w->worker, ['lease_token' => $lease, 'reason' => 'cancelled by client', 'cancelled' => true]);
    eq('cancelled', $r->json['state']);
});

test('a cancelled running task is not retried even when the failure is retryable', function () {
    $w = new World();
    list($id, $lease) = $w->running();
    $w->call('POST', "/tasks/$id/cancel", $w->client);
    $r = $w->call('POST', "/worker/tasks/$id/fail", $w->worker, ['lease_token' => $lease, 'reason' => 'x', 'retryable' => true]);
    eq('failed', $r->json['state']);
});

// ---- events ----------------------------------------------------------------

test('events come back in order and after= pages them', function () {
    $w = new World();
    list($id, $lease) = $w->running();
    $w->call('POST', "/worker/tasks/$id/events", $w->worker, ['lease_token' => $lease, 'kind' => 'phase', 'message' => 'ResolveAgent']);
    $w->call('POST', "/worker/tasks/$id/events", $w->worker, ['lease_token' => $lease, 'kind' => 'progress', 'message' => 'reading files']);
    $all = $w->call('GET', "/tasks/$id/events", $w->client)->json['events'];
    $msgs = array_map(function ($e) { return $e['message']; }, $all);
    ok(count($all) >= 3, 'expected state + two events');
    eq(['ResolveAgent', 'reading files'], array_slice($msgs, -2));
    $seqs = array_map(function ($e) { return $e['seq']; }, $all);
    $sorted = $seqs; sort($sorted);
    eq($sorted, $seqs, 'event order');
    $after = $w->call('GET', "/tasks/$id/events", $w->client, null, ['after' => (string) $seqs[count($seqs) - 2]])->json['events'];
    eq(1, count($after));
    eq('reading files', $after[0]['message']);
});

test('a worker cannot post an event of an internal kind', function () {
    $w = new World();
    list($id, $lease) = $w->running();
    eq(422, $w->call('POST', "/worker/tasks/$id/events", $w->worker, ['lease_token' => $lease, 'kind' => 'state', 'message' => 'succeeded'])->status);
});

// ---- routing ---------------------------------------------------------------

test('unknown routes and malformed ids are 404; wrong methods are 405', function () {
    $w = new World();
    eq(404, $w->call('GET', '/nope', $w->client)->status);
    eq(404, $w->call('GET', '/tasks/../../etc/passwd', $w->client)->status);
    eq(404, $w->call('GET', '/tasks/t_zz', $w->client)->status);
    $id = $w->submit()->json['task_id'];
    eq(405, $w->call('DELETE', "/tasks/$id", $w->client)->status);
    eq(405, $w->call('GET', "/worker/tasks/$id/claim", $w->worker)->status);
});

test('list agents shows only enabled agents', function () {
    $w = new World();
    $names = array_map(function ($a) { return $a['name']; }, $w->call('GET', '/agents', $w->client)->json['agents']);
    eq(['codex', 'hello-world'], $names);
    $w->pdo->exec("UPDATE agent_registry SET enabled = 0 WHERE name = 'hello-world'");
    eq(1, count($w->call('GET', '/agents', $w->client)->json['agents']));
});

test('migrate is idempotent and reseeding disables dropped agents', function () {
    $w = new World();
    $again = Bootstrap::migrate($w->pdo, ['agents' => [['name' => 'codex', 'display_name' => 'Codex', 'description' => 'd',
        'operations' => ['inspect'], 'workspaces' => ['agent_blocks']]]], __DIR__ . '/../migrations');
    eq([], $again);
    eq(1, count($w->call('GET', '/agents', $w->client)->json['agents']));
});

// ---- MCP -------------------------------------------------------------------

test('MCP requires a client token', function () {
    $w = new World();
    eq(401, $w->call('POST', '/mcp', null, ['jsonrpc' => '2.0', 'id' => 1, 'method' => 'initialize'])->status);
    eq(401, $w->mcp('initialize', [], 1, $w->worker)->status);
    eq(405, $w->call('GET', '/mcp', $w->client)->status);
});

test('MCP initialize negotiates a version and advertises tools', function () {
    $w = new World();
    $r = $w->mcp('initialize', ['protocolVersion' => '2025-03-26']);
    eq(200, $r->status);
    eq('2025-03-26', $r->json['result']['protocolVersion']);
    ok(isset($r->json['result']['capabilities']['tools']), 'no tools capability');
    eq('2025-06-18', $w->mcp('initialize', ['protocolVersion' => '1999-01-01'])->json['result']['protocolVersion']);
});

test('MCP notifications get 202 and unknown methods -32601', function () {
    $w = new World();
    eq(202, $w->mcp('notifications/initialized', [], null)->status);
    eq(-32601, $w->mcp('resources/list')->json['error']['code']);
    eq(400, $w->call('POST', '/mcp', $w->client, ['method' => 'x'])->status);
});

test('MCP tools/list names the five tools with read-only hints on the reads', function () {
    $w     = new World();
    $tools = $w->mcp('tools/list')->json['result']['tools'];
    $names = array_map(function ($t) { return $t['name']; }, $tools);
    sort($names);
    eq(['get_agent_task_result', 'get_agent_task_status', 'list_available_agents', 'search_agent_memory', 'submit_agent_task'], $names);
    foreach ($tools as $t) {
        eq(false, $t['annotations']['destructiveHint'], $t['name']);
        if (strpos($t['name'], 'get_') === 0 || $t['name'] === 'list_available_agents') {
            eq(true, $t['annotations']['readOnlyHint'], $t['name']);
        }
    }
    // the JSON must encode empty properties as an object, not []
    ok(strpos(json_encode($tools), '"properties":{}') !== false, 'empty properties encoded as a list');
});

test('MCP submit, status and result share the REST path and policy', function () {
    $w   = new World();
    $sub = $w->mcp('tools/call', ['name' => 'submit_agent_task', 'arguments' => [
        'agent' => 'codex', 'workspace' => 'agent_blocks', 'operation' => 'explain', 'prompt' => 'explain Toyota voice']])->json['result'];
    eq(false, $sub['isError']);
    $id = $sub['structuredContent']['task_id'];
    $st = $w->mcp('tools/call', ['name' => 'get_agent_task_status', 'arguments' => ['task_id' => $id]])->json['result']['structuredContent'];
    eq('queued', $st['state']);
    eq('state', $st['recent_events'][0]['kind']);
    eq('queued', $st['recent_events'][0]['message']);
    $bad = $w->mcp('tools/call', ['name' => 'submit_agent_task', 'arguments' => [
        'agent' => 'codex', 'workspace' => 'agent_blocks', 'operation' => 'modify_code', 'prompt' => 'x']])->json['result'];
    eq(true, $bad['isError']);
    ok(strpos($bad['content'][0]['text'], 'not_allowed') === 0, 'policy refusal not surfaced');
    $extra = $w->mcp('tools/call', ['name' => 'submit_agent_task', 'arguments' => [
        'agent' => 'codex', 'workspace' => 'agent_blocks', 'operation' => 'explain', 'prompt' => 'x', 'shell' => 'ls']])->json['result'];
    eq(true, $extra['isError']);
    eq(true, $w->mcp('tools/call', ['name' => 'get_agent_task_status', 'arguments' => ['task_id' => 't_nope']])->json['result']['isError']);
    eq(true, $w->mcp('tools/call', ['name' => 'rm_rf'])->json['result']['isError']);
});

test('MCP search_agent_memory queues a read-only search_memory task', function () {
    $w   = new World();
    $out = $w->mcp('tools/call', ['name' => 'search_agent_memory', 'arguments' => ['query' => 'Toyota voice']])->json['result'];
    eq(false, $out['isError']);
    eq('search_memory', $out['structuredContent']['operation']);
    eq('read_only', $out['structuredContent']['approval_mode']);
});

test('MCP list_available_agents', function () {
    $w   = new World();
    $out = $w->mcp('tools/call', ['name' => 'list_available_agents'])->json['result'];
    eq(2, count($out['structuredContent']['agents']));
});

// ---- OAuth (the ChatGPT connector) -----------------------------------------

const BASE     = 'https://bridge.example.test';
const MCP_URL  = BASE . '/api/agent-bridge/v1/mcp';
const REDIRECT = 'https://chatgpt.com/connector_platform_oauth_redirect';
const OWNER    = 'correct horse battery staple';
const VERIFIER = 'a-verifier-that-is-long-enough-to-satisfy-pkce-1234567890';

/** A World with a public address, an owner passphrase, and helpers that walk the OAuth flow. */
final class OAuthWorld
{
    public $w;

    public function __construct()
    {
        $this->w = new World(['public_base' => BASE, 'api_prefix' => '/api/agent-bridge/v1']);
        OAuthStore::setPassphrase($this->w->pdo, $this->w->clock->now(), OWNER);
    }

    public function form(string $method, string $path, array $fields): Response
    {
        $body = $method === 'GET' ? '' : http_build_query($fields);
        $q    = $method === 'GET' ? $fields : [];
        return $this->w->app->handle(new Request($method, $path, ['Content-Type' => 'application/x-www-form-urlencoded'], $body, $q));
    }

    public function register(array $uris = [REDIRECT]): Response
    {
        return $this->w->call('POST', '/oauth/register', null, ['client_name' => 'ChatGPT', 'redirect_uris' => $uris]);
    }

    public function authorizeQuery(string $clientId, array $over = []): array
    {
        return $over + ['response_type' => 'code', 'client_id' => $clientId, 'redirect_uri' => REDIRECT, 'state' => 'st8',
                        'code_challenge' => OAuthStore::s256(VERIFIER), 'code_challenge_method' => 'S256',
                        'scope' => 'tasks', 'resource' => MCP_URL];
    }

    /** The consent page's hidden ticket. */
    public function ticket(Response $page): string
    {
        ok(preg_match('/name="ticket" value="([^"]+)"/', (string) $page->text, $m) === 1, 'no ticket on the consent page');
        return html_entity_decode($m[1]);
    }

    public function approve(string $ticket, string $passphrase = OWNER): Response
    {
        return $this->form('POST', '/oauth/authorize', ['ticket' => $ticket, 'passphrase' => $passphrase, 'decision' => 'approve']);
    }

    /** Query parameters of a redirect's Location. */
    public static function back(Response $r): array
    {
        eq(302, $r->status, 'expected a redirect');
        parse_str((string) parse_url($r->headers['Location'], PHP_URL_QUERY), $q);
        return $q;
    }

    /** Registration, consent, and code exchange. Returns [client_id, code, token response]. */
    public function connect(): array
    {
        $client = $this->register()->json['client_id'];
        $code   = self::back($this->approve($this->ticket($this->form('GET', '/oauth/authorize', $this->authorizeQuery($client)))))['code'];
        return [$client, $code, $this->exchange($client, $code)];
    }

    public function exchange(string $client, string $code, string $verifier = VERIFIER): Response
    {
        return $this->form('POST', '/oauth/token', ['grant_type' => 'authorization_code', 'client_id' => $client, 'code' => $code,
                                                    'redirect_uri' => REDIRECT, 'code_verifier' => $verifier, 'resource' => MCP_URL]);
    }

    public function refresh(string $client, string $token): Response
    {
        return $this->form('POST', '/oauth/token', ['grant_type' => 'refresh_token', 'client_id' => $client, 'refresh_token' => $token]);
    }
}

test('OAuth: both metadata documents describe this gateway', function () {
    $o = new OAuthWorld();
    foreach (['/.well-known/oauth-protected-resource', '/.well-known/oauth-protected-resource/api/agent-bridge/v1/mcp'] as $path) {
        $prm = $o->w->call('GET', $path, null);
        eq(200, $prm->status, $path);
        eq(MCP_URL, $prm->json['resource']);
        eq([BASE], $prm->json['authorization_servers']);
    }
    $as = $o->w->call('GET', '/.well-known/oauth-authorization-server', null)->json;
    eq(BASE, $as['issuer']);
    eq(BASE . '/api/agent-bridge/v1/oauth/token', $as['token_endpoint']);
    eq(['S256'], $as['code_challenge_methods_supported']);
    eq(['none'], $as['token_endpoint_auth_methods_supported']);
    eq(true, $as['authorization_response_iss_parameter_supported']);
});

test('OAuth: a 401 from /mcp points at the resource metadata', function () {
    $o = new OAuthWorld();
    $r = $o->w->app->handle(new Request('POST', '/mcp', [], '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'));
    eq(401, $r->status);
    ok(strpos($r->headers['WWW-Authenticate'], 'resource_metadata="' . BASE . '/.well-known/oauth-protected-resource/api/agent-bridge/v1/mcp"') !== false,
       'challenge lacks resource_metadata');
});

test('OAuth: registration accepts ChatGPT redirects and nothing else', function () {
    $o = new OAuthWorld();
    eq(201, $o->register()->status);
    eq(201, $o->register(['https://chatgpt.com/connector/oauth/abc_DEF-123'])->status);
    eq('invalid_redirect_uri', $o->register(['https://evil.example/cb'])->json['error']);
    eq('invalid_redirect_uri', $o->register(['https://chatgpt.com.evil.example/connector_platform_oauth_redirect'])->json['error']);
    eq('invalid_redirect_uri', $o->register([])->json['error']);
    $secret = $o->w->call('POST', '/oauth/register', null, ['redirect_uris' => [REDIRECT], 'token_endpoint_auth_method' => 'client_secret_basic']);
    eq('invalid_client_metadata', $secret->json['error']);
});

test('OAuth: the full flow gives a token that works on /mcp as its own client', function () {
    $o = new OAuthWorld();
    list($client, $code, $tokens) = $o->connect();
    eq(200, $tokens->status);
    eq('Bearer', $tokens->json['token_type']);
    eq('no-cache', $tokens->headers['Pragma']);
    $listed = $o->w->mcp('tools/list', [], 1, $tokens->json['access_token']);
    eq(200, $listed->status);
    eq(5, count($listed->json['result']['tools']));
    $task = $o->w->mcp('tools/call', ['name' => 'submit_agent_task',
                                      'arguments' => ['agent' => 'codex', 'workspace' => 'agent_blocks', 'operation' => 'inspect', 'prompt' => 'hi']], 2, $tokens->json['access_token']);
    $id   = $task->json['result']['structuredContent']['task_id'];
    eq(404, $o->w->call('GET', "/tasks/$id", $o->w->client)->status, 'the static client must not see the OAuth task');
    eq(200, $o->w->call('GET', "/tasks/$id", $tokens->json['access_token'])->status);
});

test('OAuth: a wrong passphrase is refused, and five lock sign-in', function () {
    $o      = new OAuthWorld();
    $client = $o->register()->json['client_id'];
    $ticket = $o->ticket($o->form('GET', '/oauth/authorize', $o->authorizeQuery($client)));
    for ($i = 0; $i < 5; $i++) {
        $r = $o->approve($ticket, 'wrong passphrase!!');
        eq(401, $r->status);
        ok(!isset($r->headers['Location']), 'a wrong passphrase must not redirect');
    }
    eq(429, $o->approve($ticket)->status, 'the right passphrase is refused while locked');
    $o->w->clock->t += 901;
    $ticket = $o->ticket($o->form('GET', '/oauth/authorize', $o->authorizeQuery($client)));
    ok(isset(OAuthWorld::back($o->approve($ticket))['code']), 'sign-in works again after the window');
});

test('OAuth: deny returns access_denied with state and iss', function () {
    $o      = new OAuthWorld();
    $client = $o->register()->json['client_id'];
    $ticket = $o->ticket($o->form('GET', '/oauth/authorize', $o->authorizeQuery($client)));
    $q      = OAuthWorld::back($o->form('POST', '/oauth/authorize', ['ticket' => $ticket, 'decision' => 'deny']));
    eq(['error' => 'access_denied', 'state' => 'st8', 'iss' => BASE], $q);
});

test('OAuth: an unknown client or unregistered redirect gets a page, never a redirect', function () {
    $o      = new OAuthWorld();
    $client = $o->register()->json['client_id'];
    $r      = $o->form('GET', '/oauth/authorize', $o->authorizeQuery('c_nobody'));
    eq(400, $r->status);
    ok(!isset($r->headers['Location']), 'unknown client redirected');
    $r = $o->form('GET', '/oauth/authorize', $o->authorizeQuery($client, ['redirect_uri' => 'https://chatgpt.com/connector/oauth/other']));
    eq(400, $r->status);
    ok(!isset($r->headers['Location']), 'unregistered redirect followed');
});

test('OAuth: PKCE, scope and resource are checked before consent', function () {
    $o      = new OAuthWorld();
    $client = $o->register()->json['client_id'];
    eq('invalid_request', OAuthWorld::back($o->form('GET', '/oauth/authorize', $o->authorizeQuery($client, ['code_challenge' => ''])))['error']);
    eq('invalid_request', OAuthWorld::back($o->form('GET', '/oauth/authorize', $o->authorizeQuery($client, ['code_challenge_method' => 'plain'])))['error']);
    eq('invalid_scope', OAuthWorld::back($o->form('GET', '/oauth/authorize', $o->authorizeQuery($client, ['scope' => 'admin'])))['error']);
    eq('invalid_target', OAuthWorld::back($o->form('GET', '/oauth/authorize', $o->authorizeQuery($client, ['resource' => 'https://other.example/mcp'])))['error']);
    eq('unsupported_response_type', OAuthWorld::back($o->form('GET', '/oauth/authorize', $o->authorizeQuery($client, ['response_type' => 'token'])))['error']);
});

test('OAuth: a wrong verifier, an expired code, and a reused code are refused', function () {
    $o      = new OAuthWorld();
    $client = $o->register()->json['client_id'];
    $code   = function () use ($o, $client) {
        return OAuthWorld::back($o->approve($o->ticket($o->form('GET', '/oauth/authorize', $o->authorizeQuery($client)))))['code'];
    };
    eq('invalid_grant', $o->exchange($client, $code(), str_repeat('x', 50))->json['error']);
    $late = $code();
    $o->w->clock->t += 61;
    eq('invalid_grant', $o->exchange($client, $late)->json['error']);
    $once   = $code();
    $tokens = $o->exchange($client, $once)->json;
    eq(200, $o->w->mcp('tools/list', [], 1, $tokens['access_token'])->status);
    eq('invalid_grant', $o->exchange($client, $once)->json['error']);
    eq(401, $o->w->mcp('tools/list', [], 1, $tokens['access_token'])->status, 'reusing a code must revoke what it made');
});

test('OAuth: refresh tokens rotate, and replaying one revokes the connection', function () {
    $o = new OAuthWorld();
    list($client, , $first) = $o->connect();
    $second = $o->refresh($client, $first->json['refresh_token']);
    eq(200, $second->status);
    ok($second->json['refresh_token'] !== $first->json['refresh_token'], 'refresh token did not rotate');
    eq(200, $o->w->mcp('tools/list', [], 1, $second->json['access_token'])->status);
    eq('invalid_grant', $o->refresh($client, $first->json['refresh_token'])->json['error']);
    eq(401, $o->w->mcp('tools/list', [], 1, $second->json['access_token'])->status, 'replay must revoke the grant');
    eq('invalid_grant', $o->refresh($client, $second->json['refresh_token'])->json['error']);
});

test('OAuth: access tokens expire', function () {
    $o = new OAuthWorld();
    list(, , $tokens) = $o->connect();
    $o->w->clock->t += 3601;
    eq(401, $o->w->mcp('tools/list', [], 1, $tokens->json['access_token'])->status);
});

test('OAuth: a tampered or expired consent ticket is refused', function () {
    $o      = new OAuthWorld();
    $client = $o->register()->json['client_id'];
    $ticket = $o->ticket($o->form('GET', '/oauth/authorize', $o->authorizeQuery($client)));
    list($payload, $mac) = explode('.', $ticket);
    $ask = json_decode(base64_decode(strtr($payload, '-_', '+/')), true);
    $ask['redirect_uri'] = 'https://evil.example/cb';
    $forged = rtrim(strtr(base64_encode(json_encode($ask, JSON_UNESCAPED_SLASHES)), '+/', '-_'), '=') . '.' . $mac;
    eq(400, $o->approve($forged)->status);
    $o->w->clock->t += 601;
    eq(400, $o->approve($ticket)->status);
});

test('OAuth: only hashes are stored, and the audit holds no secrets', function () {
    $o = new OAuthWorld();
    list(, $code, $tokens) = $o->connect();
    $o->approve($o->ticket($o->form('GET', '/oauth/authorize', $o->authorizeQuery($o->register()->json['client_id']))), 'a wrong one here');
    $dump = '';
    foreach (['oauth_codes', 'oauth_tokens', 'oauth_owner', 'audit_events'] as $table) {
        $dump .= json_encode($o->w->pdo->query("SELECT * FROM $table")->fetchAll());
    }
    foreach ([$code, $tokens->json['access_token'], $tokens->json['refresh_token'], OWNER, 'a wrong one here'] as $secret) {
        ok(strpos($dump, $secret) === false, 'a secret was stored in the clear');
    }
});

test('OAuth: a rejected bearer is audited; a missing one is not', function () {
    $o     = new OAuthWorld();
    $count = function () use ($o) {
        return (int) $o->w->pdo->query("SELECT COUNT(*) FROM audit_events WHERE action = 'authenticate'")->fetchColumn();
    };
    $o->w->app->handle(new Request('POST', '/mcp', [], '{}'));
    eq(0, $count());
    $o->w->mcp('tools/list', [], 1, 'not_a_real_token_at_all_123');
    eq(1, $count());
});

test('OAuth: the consent page escapes the client name and cannot be framed', function () {
    $o      = new OAuthWorld();
    $client = $o->w->call('POST', '/oauth/register', null, ['client_name' => '<script>x</script>', 'redirect_uris' => [REDIRECT]])->json['client_id'];
    $page   = $o->form('GET', '/oauth/authorize', $o->authorizeQuery($client));
    eq(200, $page->status);
    ok(strpos($page->text, '<script>') === false, 'client name not escaped');
    ok(strpos($page->headers['Content-Security-Policy'], "frame-ancestors 'none'") !== false, 'page can be framed');
    eq('DENY', $page->headers['X-Frame-Options']);
});

test('OAuth: no passphrase set means no codes', function () {
    $o = new OAuthWorld();
    $o->w->pdo->exec('DELETE FROM oauth_owner');
    $client = $o->register()->json['client_id'];
    eq(503, $o->approve($o->ticket($o->form('GET', '/oauth/authorize', $o->authorizeQuery($client))))->status);
});

require __DIR__ . '/write_access_tests.php';
require __DIR__ . '/client_scope_tests.php';
require __DIR__ . '/scanner_tests.php';
require __DIR__ . '/printer_tests.php';
require __DIR__ . '/browser_tests.php';

// ---- run -------------------------------------------------------------------

foreach ($tests as $name => $body) {
    try {
        $body();
        echo "ok   $name\n";
    } catch (\Throwable $e) {
        $fails++;
        echo "FAIL $name\n     " . get_class($e) . ': ' . $e->getMessage() . ' (' . basename($e->getFile()) . ':' . $e->getLine() . ")\n";
    }
}
echo "\n" . (count($tests) - $fails) . ' passed, ' . $fails . " failed\n";
exit($fails === 0 ? 0 : 1);
