<?php
declare(strict_types=1);

namespace AgentBridge;

use PDO;

/*
 * OAuth 2.1 for one owner, so ChatGPT's MCP connector can sign in to this
 * gateway. The flow is the one ChatGPT documents for custom connectors:
 *
 *   1. ChatGPT POSTs /mcp with no token and gets a 401 whose WWW-Authenticate
 *      names the protected-resource metadata.
 *   2. It reads that, then the authorization-server metadata, and registers
 *      itself (RFC 7591). Only chatgpt.com redirect URIs are accepted.
 *   3. It sends EG's browser to /oauth/authorize with PKCE (S256 only). EG
 *      types the owner passphrase and approves; the browser goes back to
 *      ChatGPT with a 60-second, single-use code and `iss`.
 *   4. ChatGPT trades the code (plus its PKCE verifier) for a one-hour access
 *      token and a 30-day refresh token. Refresh tokens rotate on every use;
 *      replaying a used one revokes the whole connection.
 *
 * Every token and code is stored only as a sha256 hash. The passphrase is
 * stored only as password_hash(). An OAuth grant becomes a `client`
 * Principal, so it gets exactly what a client token gets: the read-only
 * policy in Policy.php, enforced server side.
 */

/** An OAuth protocol error, carried back to the client as {error, error_description}. */
final class OAuthError extends \RuntimeException
{
    /** @var string */ public $error;
    /** @var int */    public $status;

    public function __construct(string $error, string $description, int $status = 400)
    {
        parent::__construct($description);
        $this->error  = $error;
        $this->status = $status;
    }
}

/** The values the OAuth pieces share, worked out once from the config. */
final class OAuthSettings
{
    const SCOPE = 'tasks';
    const DEFAULTS = [
        'access_ttl'        => 3600,
        'refresh_ttl'       => 2592000,
        'code_ttl'          => 60,
        'ticket_ttl'        => 600,
        'max_clients'       => 50,
        'max_failures'      => 5,
        'failure_window'    => 900,
        'redirect_patterns' => [
            '#^https://chatgpt\.com/connector_platform_oauth_redirect$#',
            '#^https://chatgpt\.com/connector/oauth/[A-Za-z0-9_-]{1,128}$#',
        ],
    ];

    /** @var string */   public $issuer;
    /** @var string */   public $resource;
    /** @var string */   public $endpoints;
    /** @var int */      public $accessTtl;
    /** @var int */      public $refreshTtl;
    /** @var int */      public $codeTtl;
    /** @var int */      public $ticketTtl;
    /** @var int */      public $maxClients;
    /** @var int */      public $maxFailures;
    /** @var int */      public $failureWindow;
    /** @var string[] */ public $redirectPatterns;

    public static function fromConfig(array $config): OAuthSettings
    {
        $o      = (isset($config['oauth']) && is_array($config['oauth']) ? $config['oauth'] : []) + self::DEFAULTS;
        $prefix = rtrim((string) ($config['api_prefix'] ?? '/api/agent-bridge/v1'), '/');
        $s      = new OAuthSettings();
        $s->issuer           = rtrim((string) ($config['public_base'] ?? 'http://localhost'), '/');
        $s->resource         = $s->issuer . $prefix . '/mcp';
        $s->endpoints        = $s->issuer . $prefix . '/oauth';
        $s->accessTtl        = (int) $o['access_ttl'];
        $s->refreshTtl       = (int) $o['refresh_ttl'];
        $s->codeTtl          = (int) $o['code_ttl'];
        $s->ticketTtl        = (int) $o['ticket_ttl'];
        $s->maxClients       = (int) $o['max_clients'];
        $s->maxFailures      = (int) $o['max_failures'];
        $s->failureWindow    = (int) $o['failure_window'];
        $s->redirectPatterns = array_values($o['redirect_patterns']);
        return $s;
    }

    /** RFC 9728's path-inserted metadata URL for the MCP resource. */
    public function resourceMetadataUrl(): string
    {
        return $this->issuer . '/.well-known/oauth-protected-resource' . (string) parse_url($this->resource, PHP_URL_PATH);
    }

    /** The header every 401 carries, so a client can find out how to sign in. */
    public function challenge(): array
    {
        return ['WWW-Authenticate' => sprintf('Bearer realm="agent-bridge", resource_metadata="%s", scope="%s"',
                                              $this->resourceMetadataUrl(), self::SCOPE)];
    }

    public function isResource(string $resource): bool
    {
        return rtrim($resource, '/') === $this->resource;
    }

    public function allowsRedirect(string $uri): bool
    {
        foreach ($this->redirectPatterns as $pattern) {
            if (preg_match($pattern, $uri)) {
                return true;
            }
        }
        return false;
    }
}

/** The SQLite tables behind OAuth (migrations/002_oauth.sql). */
final class OAuthStore
{
    /** @var PDO */          private $pdo;
    /** @var IClock */       private $clock;
    /** @var IIdGenerator */ private $ids;

    public function __construct(PDO $pdo, IClock $clock, IIdGenerator $ids)
    {
        $this->pdo   = $pdo;
        $this->clock = $clock;
        $this->ids   = $ids;
    }

    // ---- clients ----------------------------------------------------------

    /** Registers a client. Forgets day-old registrations that never connected first. */
    public function addClient(string $name, array $redirectUris, int $maxClients): ?array
    {
        return Database::immediate($this->pdo, function () use ($name, $redirectUris, $maxClients) {
            $now = $this->clock->now();
            $this->pdo->prepare('DELETE FROM oauth_clients WHERE created_at < ?
                                   AND id NOT IN (SELECT client_id FROM oauth_grants)
                                   AND id NOT IN (SELECT client_id FROM oauth_codes)')
                      ->execute([$now - 86400]);
            if ((int) $this->pdo->query('SELECT COUNT(*) FROM oauth_clients')->fetchColumn() >= $maxClients) {
                return null;
            }
            $client = ['id' => $this->ids->newId('c_'), 'name' => $name, 'redirect_uris' => $redirectUris, 'created_at' => $now];
            $this->pdo->prepare('INSERT INTO oauth_clients (id, name, redirect_uris, created_at) VALUES (?, ?, ?, ?)')
                      ->execute([$client['id'], $name, json_encode($redirectUris, JSON_UNESCAPED_SLASHES), $now]);
            return $client;
        });
    }

    public function client(string $id): ?array
    {
        $stmt = $this->pdo->prepare('SELECT id, name, redirect_uris FROM oauth_clients WHERE id = ?');
        $stmt->execute([$id]);
        $row = $stmt->fetch();
        if (!$row) {
            return null;
        }
        $row['redirect_uris'] = json_decode((string) $row['redirect_uris'], true) ?: [];
        return $row;
    }

    // ---- the owner --------------------------------------------------------

    public static function setPassphrase(PDO $pdo, int $now, string $passphrase): void
    {
        $pdo->prepare('INSERT INTO oauth_owner (id, passphrase_hash, updated_at) VALUES (1, ?, ?)
                       ON CONFLICT (id) DO UPDATE SET passphrase_hash = excluded.passphrase_hash, updated_at = excluded.updated_at')
            ->execute([password_hash($passphrase, PASSWORD_DEFAULT), $now]);
    }

    public function passphraseHash(): ?string
    {
        $hash = $this->pdo->query('SELECT passphrase_hash FROM oauth_owner WHERE id = 1')->fetchColumn();
        return $hash === false ? null : (string) $hash;
    }

    public function recentFailures(int $window): int
    {
        $stmt = $this->pdo->prepare('SELECT COUNT(*) FROM oauth_failures WHERE at > ?');
        $stmt->execute([$this->clock->now() - $window]);
        return (int) $stmt->fetchColumn();
    }

    public function recordFailure(int $window): void
    {
        $now = $this->clock->now();
        $this->pdo->prepare('DELETE FROM oauth_failures WHERE at <= ?')->execute([$now - $window]);
        $this->pdo->prepare('INSERT INTO oauth_failures (at) VALUES (?)')->execute([$now]);
    }

    public function clearFailures(): void
    {
        $this->pdo->exec('DELETE FROM oauth_failures');
    }

    /** The HMAC key for consent tickets, made by the migration with randomblob(). */
    public function ticketKey(): string
    {
        return (string) $this->pdo->query("SELECT secret FROM oauth_keys WHERE name = 'ticket'")->fetchColumn();
    }

    // ---- codes and tokens -------------------------------------------------

    /** Stores a single-use authorization code and returns it. */
    public function addCode(array $ask, int $ttl): string
    {
        $code = $this->ids->newSecret();
        $now  = $this->clock->now();
        $this->pdo->prepare('DELETE FROM oauth_codes WHERE expires_at < ? AND grant_id IS NULL')->execute([$now - 3600]);
        $this->pdo->prepare('INSERT INTO oauth_codes (code_hash, client_id, redirect_uri, code_challenge, scope, resource, expires_at)
                             VALUES (?, ?, ?, ?, ?, ?, ?)')
                  ->execute([hash('sha256', $code), $ask['client_id'], $ask['redirect_uri'], $ask['challenge'],
                             $ask['scope'], $ask['resource'], $now + $ttl]);
        return $code;
    }

    /** Trades a code for tokens. A second use of a code revokes what the first one made. */
    public function redeemCode(string $code, string $clientId, string $redirectUri, string $verifier, OAuthSettings $s): array
    {
        $out = Database::immediate($this->pdo, function () use ($code, $clientId, $redirectUri, $verifier, $s) {
            $stmt = $this->pdo->prepare('SELECT * FROM oauth_codes WHERE code_hash = ?');
            $stmt->execute([hash('sha256', $code)]);
            $row = $stmt->fetch();
            if (!$row) {
                return new OAuthError('invalid_grant', 'unknown authorization code');
            }
            if ($row['grant_id'] !== null) {
                $this->revokeGrant((string) $row['grant_id']);
                return new OAuthError('invalid_grant', 'authorization code already used; the connection it made is revoked');
            }
            if ((int) $row['expires_at'] <= $this->clock->now()) {
                return new OAuthError('invalid_grant', 'authorization code expired');
            }
            if ($row['client_id'] !== $clientId || $row['redirect_uri'] !== $redirectUri) {
                return new OAuthError('invalid_grant', 'client_id or redirect_uri does not match the authorization request');
            }
            if (!preg_match('/^[A-Za-z0-9._~-]{43,128}$/', $verifier)
                || !hash_equals((string) $row['code_challenge'], self::s256($verifier))) {
                return new OAuthError('invalid_grant', 'code_verifier does not match the code_challenge');
            }
            $grantId = $this->ids->newId('g_');
            $this->pdo->prepare('INSERT INTO oauth_grants (id, client_id, scope, resource, created_at) VALUES (?, ?, ?, ?, ?)')
                      ->execute([$grantId, $clientId, $row['scope'], $row['resource'], $this->clock->now()]);
            $this->pdo->prepare('UPDATE oauth_codes SET grant_id = ? WHERE code_hash = ?')->execute([$grantId, $row['code_hash']]);
            return $this->issuePair($grantId, (string) $row['scope'], $s);
        });
        if ($out instanceof OAuthError) {
            throw $out;
        }
        return $out;
    }

    /** Rotates a refresh token. Replaying a used one revokes the connection: one of the two copies was stolen. */
    public function refresh(string $token, string $clientId, OAuthSettings $s): array
    {
        $out = Database::immediate($this->pdo, function () use ($token, $clientId, $s) {
            $stmt = $this->pdo->prepare("SELECT t.token_hash, t.grant_id, t.expires_at, t.used, g.client_id, g.scope, g.revoked
                                           FROM oauth_tokens t JOIN oauth_grants g ON g.id = t.grant_id
                                          WHERE t.token_hash = ? AND t.kind = 'refresh'");
            $stmt->execute([hash('sha256', $token)]);
            $row = $stmt->fetch();
            if (!$row || (int) $row['revoked'] === 1 || $row['client_id'] !== $clientId) {
                return new OAuthError('invalid_grant', 'unknown or revoked refresh token');
            }
            if ((int) $row['used'] === 1) {
                $this->revokeGrant((string) $row['grant_id']);
                return new OAuthError('invalid_grant', 'refresh token already used; the connection is revoked, reconnect in ChatGPT');
            }
            if ((int) $row['expires_at'] <= $this->clock->now()) {
                return new OAuthError('invalid_grant', 'refresh token expired');
            }
            $this->pdo->prepare('UPDATE oauth_tokens SET used = 1 WHERE token_hash = ?')->execute([$row['token_hash']]);
            return $this->issuePair((string) $row['grant_id'], (string) $row['scope'], $s);
        });
        if ($out instanceof OAuthError) {
            throw $out;
        }
        return $out;
    }

    /** The grant behind a live access token for this resource, or null. */
    public function accessGrant(string $token, string $resource): ?array
    {
        $stmt = $this->pdo->prepare("SELECT g.id, c.name
                                       FROM oauth_tokens t
                                       JOIN oauth_grants g ON g.id = t.grant_id
                                       JOIN oauth_clients c ON c.id = g.client_id
                                      WHERE t.token_hash = ? AND t.kind = 'access' AND t.expires_at > ?
                                        AND g.revoked = 0 AND g.resource = ?");
        $stmt->execute([hash('sha256', $token), $this->clock->now(), $resource]);
        $row = $stmt->fetch();
        return $row ?: null;
    }

    public function revokeGrant(string $grantId): bool
    {
        $stmt = $this->pdo->prepare('UPDATE oauth_grants SET revoked = 1 WHERE id = ? AND revoked = 0');
        $stmt->execute([$grantId]);
        return $stmt->rowCount() === 1;
    }

    /** For bin/oauth_admin.php: the live connections, newest first. */
    public function grants(): array
    {
        return $this->pdo->query('SELECT g.id, c.name, g.created_at, g.revoked FROM oauth_grants g
                                    JOIN oauth_clients c ON c.id = g.client_id ORDER BY g.created_at DESC')->fetchAll();
    }

    private function issuePair(string $grantId, string $scope, OAuthSettings $s): array
    {
        $now     = $this->clock->now();
        $access  = $this->ids->newSecret();
        $refresh = $this->ids->newSecret();
        $this->pdo->prepare('DELETE FROM oauth_tokens WHERE expires_at < ?')->execute([$now]);
        $insert = $this->pdo->prepare('INSERT INTO oauth_tokens (token_hash, grant_id, kind, expires_at) VALUES (?, ?, ?, ?)');
        $insert->execute([hash('sha256', $access), $grantId, 'access', $now + $s->accessTtl]);
        $insert->execute([hash('sha256', $refresh), $grantId, 'refresh', $now + $s->refreshTtl]);
        return ['access_token' => $access, 'token_type' => 'Bearer', 'expires_in' => $s->accessTtl,
                'refresh_token' => $refresh, 'scope' => $scope];
    }

    public static function s256(string $verifier): string
    {
        return rtrim(strtr(base64_encode(hash('sha256', $verifier, true)), '+/', '-_'), '=');
    }
}

/** ITaskAuthenticator for OAuth access tokens. Each connection is its own client. */
final class OAuthAuthenticator implements ITaskAuthenticator
{
    /** @var OAuthStore */ private $store;
    /** @var string */     private $resource;

    public function __construct(OAuthStore $store, string $resource)
    {
        $this->store    = $store;
        $this->resource = $resource;
    }

    public function authenticate(Request $request): ?Principal
    {
        if (!preg_match('/^Bearer\s+([A-Za-z0-9_-]{20,200})$/', trim($request->header('authorization')), $m)) {
            return null;
        }
        $grant = $this->store->accessGrant($m[1], $this->resource);
        return $grant ? new Principal((string) $grant['id'], 'client', 'oauth: ' . $grant['name']) : null;
    }
}

/** Composite: the first authenticator that recognises the request wins. */
final class FirstMatchAuthenticator implements ITaskAuthenticator
{
    /** @var ITaskAuthenticator[] */ private $members;

    public function __construct(array $members)
    {
        $this->members = $members;
    }

    public function authenticate(Request $request): ?Principal
    {
        foreach ($this->members as $member) {
            $who = $member->authenticate($request);
            if ($who !== null) {
                return $who;
            }
        }
        return null;
    }
}

/** Decorator: audits a presented credential that nothing recognised. Requests with no credential are not logged. */
final class AuditedAuthenticator implements ITaskAuthenticator
{
    /** @var ITaskAuthenticator */ private $inner;
    /** @var IAuditLogger */       private $audit;

    public function __construct(ITaskAuthenticator $inner, IAuditLogger $audit)
    {
        $this->inner = $inner;
        $this->audit = $audit;
    }

    public function authenticate(Request $request): ?Principal
    {
        $who = $this->inner->authenticate($request);
        if ($who === null && $request->header('authorization') !== '') {
            $this->audit->record('anonymous', 'authenticate', null, 'refused', ['path' => substr($request->path, 0, 100)]);
        }
        return $who;
    }
}

/** The OAuth routes: two metadata documents, registration, consent, and the token endpoint. */
final class OAuthServer
{
    const MAX_BODY = 16384;
    const CSS = 'body{font:16px/1.5 system-ui,sans-serif;margin:0;padding:16px;background:#f4f4f1;color:#1c1c1a}'
              . 'main{max-width:28rem;margin:6vh auto;background:#fff;padding:24px;border-radius:12px;box-shadow:0 1px 3px rgba(0,0,0,.15)}'
              . 'h1{font-size:1.3rem;margin-top:0}label{display:block;font-weight:600;margin:16px 0 6px}'
              . 'input{width:100%;box-sizing:border-box;padding:12px;font-size:16px;border:1px solid #aaa;border-radius:8px}'
              . 'button{padding:12px 20px;font-size:16px;border:0;border-radius:8px;margin:16px 8px 0 0;cursor:pointer}'
              . '.go{background:#1d4ed8;color:#fff}.no{background:#e4e4e0;color:#1c1c1a}.err{color:#b91c1c;font-weight:600}'
              . '.small{font-size:.9rem;color:#555}'
              . '@media (prefers-color-scheme:dark){body{background:#151514;color:#eee}main{background:#232321}'
              . 'input{background:#151514;color:#eee;border-color:#555}.no{background:#3a3a37;color:#eee}.small{color:#aaa}.err{color:#f87171}}';

    /** @var OAuthSettings */ private $s;
    /** @var OAuthStore */    private $store;
    /** @var IAuditLogger */  private $audit;
    /** @var IClock */        private $clock;

    public function __construct(OAuthSettings $settings, OAuthStore $store, IAuditLogger $audit, IClock $clock)
    {
        $this->s     = $settings;
        $this->store = $store;
        $this->audit = $audit;
        $this->clock = $clock;
    }

    public function serves(string $path): bool
    {
        return strpos($path, '/.well-known/oauth-') === 0 || strpos($path, '/oauth/') === 0;
    }

    public function handle(Request $r): Response
    {
        if (strlen($r->body) > self::MAX_BODY) {
            return Response::error(413, 'too_large', 'request body too large');
        }
        $p = rtrim($r->path, '/');
        if ($p === '/.well-known/oauth-protected-resource'
            || $p === '/.well-known/oauth-protected-resource' . (string) parse_url($this->s->resource, PHP_URL_PATH)) {
            return $this->onlyGet($r) ?: new Response(200, $this->resourceMetadata());
        }
        if ($p === '/.well-known/oauth-authorization-server') {
            return $this->onlyGet($r) ?: new Response(200, $this->serverMetadata());
        }
        if ($p === '/oauth/register') {
            return $r->method === 'POST' ? $this->register($r) : new Response(405, null, ['Allow' => 'POST']);
        }
        if ($p === '/oauth/authorize') {
            if ($r->method === 'GET') {
                return $this->ask($r->query);
            }
            return $r->method === 'POST' ? $this->decide($r->form()) : new Response(405, null, ['Allow' => 'GET, POST']);
        }
        if ($p === '/oauth/token') {
            return $r->method === 'POST' ? $this->token($r->form()) : new Response(405, null, ['Allow' => 'POST']);
        }
        return Response::error(404, 'not_found', 'no such route');
    }

    // ---- metadata ---------------------------------------------------------

    private function resourceMetadata(): array
    {
        return ['resource' => $this->s->resource, 'authorization_servers' => [$this->s->issuer],
                'scopes_supported' => [OAuthSettings::SCOPE], 'bearer_methods_supported' => ['header'],
                'resource_name' => 'EG agent bridge'];
    }

    private function serverMetadata(): array
    {
        return [
            'issuer'                                         => $this->s->issuer,
            'authorization_endpoint'                         => $this->s->endpoints . '/authorize',
            'token_endpoint'                                 => $this->s->endpoints . '/token',
            'registration_endpoint'                          => $this->s->endpoints . '/register',
            'scopes_supported'                               => [OAuthSettings::SCOPE],
            'response_types_supported'                       => ['code'],
            'grant_types_supported'                          => ['authorization_code', 'refresh_token'],
            'token_endpoint_auth_methods_supported'          => ['none'],
            'code_challenge_methods_supported'               => ['S256'],
            'authorization_response_iss_parameter_supported' => true,
        ];
    }

    private function onlyGet(Request $r): ?Response
    {
        return $r->method === 'GET' ? null : new Response(405, null, ['Allow' => 'GET']);
    }

    // ---- registration (RFC 7591) ------------------------------------------

    private function register(Request $r): Response
    {
        $body = json_decode($r->body, true);
        if (!is_array($body)) {
            return self::oauthError('invalid_client_metadata', 'body must be a JSON object');
        }
        $uris = isset($body['redirect_uris']) ? $body['redirect_uris'] : null;
        if (!is_array($uris) || $uris === [] || count($uris) > 5) {
            return self::oauthError('invalid_redirect_uri', 'redirect_uris must list one to five URIs');
        }
        foreach ($uris as $uri) {
            if (!is_string($uri) || !$this->s->allowsRedirect($uri)) {
                return self::oauthError('invalid_redirect_uri', 'only ChatGPT connector redirect URIs are accepted');
            }
        }
        $method = isset($body['token_endpoint_auth_method']) ? $body['token_endpoint_auth_method'] : 'none';
        if ($method !== 'none') {
            return self::oauthError('invalid_client_metadata', 'token_endpoint_auth_method must be none (public client with PKCE)');
        }
        if (array_diff(self::strings($body, 'grant_types'), ['authorization_code', 'refresh_token'])
            || array_diff(self::strings($body, 'response_types'), ['code'])) {
            return self::oauthError('invalid_client_metadata', 'only the authorization_code and refresh_token grants are supported');
        }
        $name   = isset($body['client_name']) && is_string($body['client_name']) ? $body['client_name'] : 'unnamed client';
        $name   = substr((string) preg_replace('/[\x00-\x1f\x7f]/', '', $name), 0, 100);
        $client = $this->store->addClient($name, array_values($uris), $this->s->maxClients);
        if ($client === null) {
            $this->audit->record('anonymous', 'oauth_register', null, 'refused', ['reason' => 'too many clients']);
            return self::oauthError('temporarily_unavailable', 'too many registered clients; try again tomorrow', 429);
        }
        $this->audit->record('anonymous', 'oauth_register', null, 'registered', ['client_id' => $client['id'], 'client_name' => $name]);
        return new Response(201, [
            'client_id' => $client['id'], 'client_id_issued_at' => $client['created_at'], 'client_name' => $name,
            'redirect_uris' => $client['redirect_uris'], 'token_endpoint_auth_method' => 'none',
            'grant_types' => ['authorization_code', 'refresh_token'], 'response_types' => ['code'],
        ]);
    }

    // ---- consent ----------------------------------------------------------

    /** GET /oauth/authorize: check the request, then show the consent page. */
    private function ask(array $q): Response
    {
        $client = $this->store->client(self::field($q, 'client_id'));
        if ($client === null) {
            return $this->page(400, 'Unknown connection', '<p>This app is not registered here. Remove the connector in ChatGPT and add it again.</p>');
        }
        $redirect = self::field($q, 'redirect_uri');
        if ($redirect === '' && count($client['redirect_uris']) === 1) {
            $redirect = $client['redirect_uris'][0];
        }
        if (!in_array($redirect, $client['redirect_uris'], true)) {
            return $this->page(400, 'Unknown return address', '<p>The address to return to is not the one this app registered, so nothing was sent back.</p>');
        }
        // From here the redirect is trusted, so problems go back to the client.
        $state = substr(self::field($q, 'state'), 0, 500);
        if (self::field($q, 'response_type') !== 'code') {
            return $this->back($redirect, $state, ['error' => 'unsupported_response_type']);
        }
        $challenge = self::field($q, 'code_challenge');
        if (self::field($q, 'code_challenge_method') !== 'S256' || !preg_match('/^[A-Za-z0-9_-]{43,128}$/', $challenge)) {
            return $this->back($redirect, $state, ['error' => 'invalid_request', 'error_description' => 'PKCE with S256 is required']);
        }
        $asked = preg_split('/\s+/', trim(self::field($q, 'scope')), -1, PREG_SPLIT_NO_EMPTY) ?: [];
        if ($asked !== [] && !in_array(OAuthSettings::SCOPE, $asked, true)) {
            return $this->back($redirect, $state, ['error' => 'invalid_scope']);
        }
        $resource = self::field($q, 'resource');
        if ($resource !== '' && !$this->s->isResource($resource)) {
            return $this->back($redirect, $state, ['error' => 'invalid_target']);
        }
        $ticket = $this->seal(['client_id' => $client['id'], 'redirect_uri' => $redirect, 'challenge' => $challenge,
                               'state' => $state, 'scope' => OAuthSettings::SCOPE, 'resource' => $this->s->resource,
                               'exp' => $this->clock->now() + $this->s->ticketTtl]);
        return $this->consent($client['name'], $redirect, $ticket, '');
    }

    /** POST /oauth/authorize: the owner's answer. */
    private function decide(array $f): Response
    {
        $ask = $this->open(self::field($f, 'ticket'));
        if ($ask === null) {
            return $this->page(400, 'This page has expired', '<p>Start the connection again from ChatGPT.</p>');
        }
        if (self::field($f, 'decision') !== 'approve') {
            $this->audit->record('owner', 'oauth_consent', null, 'denied', ['client_id' => $ask['client_id']]);
            return $this->back($ask['redirect_uri'], $ask['state'], ['error' => 'access_denied']);
        }
        if ($this->store->recentFailures($this->s->failureWindow) >= $this->s->maxFailures) {
            $this->audit->record('anonymous', 'oauth_consent', null, 'locked', ['client_id' => $ask['client_id']]);
            return $this->page(429, 'Too many wrong passphrases',
                               '<p>Sign-in is paused for ' . intdiv($this->s->failureWindow, 60) . ' minutes. Then start again from ChatGPT.</p>');
        }
        $hash = $this->store->passphraseHash();
        if ($hash === null) {
            return $this->page(503, 'Not set up yet', '<p>No owner passphrase has been set on the gateway. Run bin/oauth_admin.php passphrase on the server.</p>');
        }
        if (!password_verify(self::field($f, 'passphrase'), $hash)) {
            $this->store->recordFailure($this->s->failureWindow);
            $this->audit->record('anonymous', 'oauth_consent', null, 'bad_passphrase', ['client_id' => $ask['client_id']]);
            $client = $this->store->client($ask['client_id']);
            return $this->consent($client ? $client['name'] : 'unknown app', $ask['redirect_uri'], self::field($f, 'ticket'),
                                  'That passphrase is not right.', 401);
        }
        $this->store->clearFailures();
        $code = $this->store->addCode($ask, $this->s->codeTtl);
        $this->audit->record('owner', 'oauth_consent', null, 'approved', ['client_id' => $ask['client_id']]);
        return $this->back($ask['redirect_uri'], $ask['state'], ['code' => $code]);
    }

    private function consent(string $client, string $redirect, string $ticket, string $error, int $status = 200): Response
    {
        $host = (string) parse_url($redirect, PHP_URL_HOST);
        $body = '<p><strong>' . self::e($client) . '</strong> wants to connect to EG\'s agent bridge.</p>'
              . '<p>If you approve, it can submit <strong>read-only</strong> tasks to the on-machine agents and read their results. '
              . 'It cannot change files.</p>'
              . '<p class="small">Afterwards you go back to ' . self::e($host) . '. Only approve if you started this from ChatGPT just now.</p>'
              . ($error !== '' ? '<p class="err">' . self::e($error) . '</p>' : '')
              . '<form method="post" action="' . self::e($this->s->endpoints . '/authorize') . '">'
              . '<input type="hidden" name="ticket" value="' . self::e($ticket) . '">'
              . '<label for="passphrase">Owner passphrase</label>'
              . '<input id="passphrase" type="password" name="passphrase" autocomplete="current-password" required autofocus>'
              . '<button class="go" name="decision" value="approve">Approve</button>'
              . '<button class="no" name="decision" value="deny" formnovalidate>Deny</button>'
              . '</form>';
        return $this->page($status, 'Connect to the agent bridge', $body);
    }

    // ---- tokens -----------------------------------------------------------

    private function token(array $f): Response
    {
        try {
            $resource = self::field($f, 'resource');
            if ($resource !== '' && !$this->s->isResource($resource)) {
                throw new OAuthError('invalid_target', 'unknown resource');
            }
            $client = self::field($f, 'client_id');
            switch (self::field($f, 'grant_type')) {
                case 'authorization_code':
                    $tokens = $this->store->redeemCode(self::field($f, 'code'), $client, self::field($f, 'redirect_uri'),
                                                       self::field($f, 'code_verifier'), $this->s);
                    break;
                case 'refresh_token':
                    $tokens = $this->store->refresh(self::field($f, 'refresh_token'), $client, $this->s);
                    break;
                default:
                    throw new OAuthError('unsupported_grant_type', 'use authorization_code or refresh_token');
            }
        } catch (OAuthError $e) {
            $this->audit->record('anonymous', 'oauth_token', null, 'refused', ['error' => $e->error]);
            return self::oauthError($e->error, $e->getMessage(), $e->status);
        }
        return new Response(200, $tokens, ['Pragma' => 'no-cache']);
    }

    // ---- helpers ----------------------------------------------------------

    /** Signs the checked authorization request into the consent form, so nothing is stored until EG approves. */
    private function seal(array $ask): string
    {
        $payload = rtrim(strtr(base64_encode((string) json_encode($ask, JSON_UNESCAPED_SLASHES)), '+/', '-_'), '=');
        return $payload . '.' . hash_hmac('sha256', $payload, $this->store->ticketKey());
    }

    private function open(string $ticket): ?array
    {
        $parts = explode('.', $ticket);
        if (count($parts) !== 2 || !hash_equals(hash_hmac('sha256', $parts[0], $this->store->ticketKey()), $parts[1])) {
            return null;
        }
        $ask = json_decode((string) base64_decode(strtr($parts[0], '-_', '+/')), true);
        return is_array($ask) && isset($ask['exp']) && (int) $ask['exp'] > $this->clock->now() ? $ask : null;
    }

    /** Sends the browser back to the client, always with `iss` (RFC 9207). */
    private function back(string $redirect, string $state, array $params): Response
    {
        if ($state !== '') {
            $params['state'] = $state;
        }
        $params['iss'] = $this->s->issuer;
        return Response::redirect($redirect . (strpos($redirect, '?') === false ? '?' : '&') . http_build_query($params));
    }

    private function page(int $status, string $title, string $body): Response
    {
        return Response::html($status, '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            . '<meta name="viewport" content="width=device-width, initial-scale=1"><title>' . self::e($title) . '</title>'
            . '<style>' . self::CSS . '</style></head><body><main><h1>' . self::e($title) . '</h1>' . $body . '</main></body></html>');
    }

    private static function oauthError(string $error, string $description, int $status = 400): Response
    {
        return new Response($status, ['error' => $error, 'error_description' => $description]);
    }

    private static function field(array $from, string $key): string
    {
        return isset($from[$key]) && is_string($from[$key]) ? $from[$key] : '';
    }

    private static function strings(array $from, string $key): array
    {
        return isset($from[$key]) && is_array($from[$key]) ? array_map('strval', $from[$key]) : [];
    }

    private static function e(string $text): string
    {
        return htmlspecialchars($text, ENT_QUOTES | ENT_HTML5, 'UTF-8');
    }
}
