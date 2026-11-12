<?php
declare(strict_types=1);

namespace AgentBridge;

use PDO;

require_once __DIR__ . '/Contracts.php';
require_once __DIR__ . '/Http.php';
require_once __DIR__ . '/TaskState.php';
require_once __DIR__ . '/SqliteStore.php';
require_once __DIR__ . '/Policy.php';
require_once __DIR__ . '/SubmitWorkflow.php';
require_once __DIR__ . '/ScanStore.php';
require_once __DIR__ . '/ScannerPolicy.php';
require_once __DIR__ . '/ScannerTools.php';
require_once __DIR__ . '/WorkerScans.php';
require_once __DIR__ . '/WorkerPins.php';
require_once __DIR__ . '/DownloadStore.php';
require_once __DIR__ . '/PrinterPolicy.php';
require_once __DIR__ . '/PrinterTools.php';
require_once __DIR__ . '/BrowserTools.php';
require_once __DIR__ . '/RelayTools.php';
require_once __DIR__ . '/NotesTools.php';
require_once __DIR__ . '/UploadStore.php';
require_once __DIR__ . '/AlertWatch.php';
require_once __DIR__ . '/RequestWatch.php';
require_once __DIR__ . '/McpEndpoint.php';
require_once __DIR__ . '/OAuth.php';
require_once __DIR__ . '/GatewayApp.php';

/** The only place that names concretes. Everything else takes interfaces. */
final class Bootstrap
{
    /** Which mode each operation runs in. Not client-settable; a gateway may narrow it in config. */
    const OPERATION_MODES = ['inspect' => 'read_only', 'explain' => 'read_only', 'search_memory' => 'read_only',
                             'implement' => 'workspace_write', 'scan' => 'device_control',
                             'print' => 'device_control', 'print_cancel' => 'device_control',
                             'print_status' => 'read_only', 'print_discover' => 'read_only'];
    const MAX_RESULT_BODY = 262144;
    const MAX_DETAILS     = 131072;
    const MAX_BLOB        = 7340032;
    const MAX_MCP_BODY    = 393216;     // one notes_upload_chunk (256 KiB of base64) and its envelope

    public static function loadConfig(string $path): array
    {
        if (!is_file($path)) {
            throw new \RuntimeException('agent-bridge config not found');
        }
        $config = require $path;
        if (!is_array($config) || !isset($config['database'])) {
            throw new \RuntimeException('agent-bridge config must return an array with a database path');
        }
        return $config + [
            'lease_seconds' => 120, 'max_attempts' => 2, 'max_body' => 65536,
            'max_result_body' => self::MAX_RESULT_BODY, 'max_details' => self::MAX_DETAILS,
            'operation_modes' => self::OPERATION_MODES,
            'allowed_operations' => ['inspect', 'explain', 'search_memory'],
            'allowed_modes' => ['read_only'],
            'memory_agent' => 'codex', 'memory_workspace' => 'agent_blocks',
            'agents' => [],
            'public_base' => 'http://localhost', 'api_prefix' => '/api/agent-bridge/v1', 'oauth' => [],
            'scanner_scopes' => [], 'scanner_max_starts_per_hour' => 12, 'max_blob' => self::MAX_BLOB,
            'scans_dir' => dirname((string) $config['database']) . '/scans',
            'printer_scopes' => [], 'printer_max_prints_per_hour' => 10, 'printer_max_open' => 2,
            'max_mcp_body' => self::MAX_MCP_BODY, 'upload_fetch_seconds' => 30,
            'watch' => [], 'requests' => [], 'alert_viewers' => [],
        ];
    }

    public static function build(array $config, ?PDO $pdo = null, ?IClock $clock = null, ?IIdGenerator $ids = null): GatewayApp
    {
        $pdo   = $pdo   ?: Database::open($config['database']);
        $clock = $clock ?: new SystemClock();
        $ids   = $ids   ?: new RandomIds();

        $store    = new SqliteTaskStore($pdo, $clock, $ids);
        $registry = new SqliteAgentRegistry($pdo);
        $audit    = new SqliteAuditLogger($pdo, $clock);
        $settings = OAuthSettings::fromConfig($config);
        $oauth    = new OAuthStore($pdo, $clock, $ids);
        $auth     = new AuditedAuthenticator(new FirstMatchAuthenticator([
            new TokenAuthenticator($pdo),
            new OAuthAuthenticator($oauth, $settings->resource),
        ]), $audit);
        $maxBody  = (int) $config['max_body'];
        $config  += ['operation_modes' => self::OPERATION_MODES, 'max_result_body' => self::MAX_RESULT_BODY,
                     'max_details' => self::MAX_DETAILS, 'scanner_scopes' => [], 'scanner_max_starts_per_hour' => 12,
                     'max_blob' => self::MAX_BLOB, 'scans_dir' => dirname((string) $config['database']) . '/scans',
                     'printer_scopes' => [], 'printer_max_prints_per_hour' => 10, 'printer_max_open' => 2,
                     'max_mcp_body' => self::MAX_MCP_BODY, 'upload_fetch_seconds' => 30];
        $scopes    = new ScannerScopes((array) $config['scanner_scopes']);
        $scanStore = new SqliteScanStore($pdo, $clock);
        $downloadStore = new SqliteDownloadStore($pdo, $clock);
        $blobs     = new FileBlobStore((string) $config['scans_dir']);
        $guard     = new ScanStartGuard($scopes, $scanStore, $store, $clock, (int) $config['scanner_max_starts_per_hour']);
        $printerScopes = new PrinterScopes((array) $config['printer_scopes']);
        $printJobs     = new SqlitePrinterJobs($pdo);
        $printGuard    = new PrintGuard($printerScopes, $printJobs, $store, $clock, (int) $config['printer_max_prints_per_hour'],
                                        (int) $config['printer_max_open']);
        $grants    = McpEndpoint::grants($config['allowed_operations'], $config['allowed_modes'], $config['operation_modes']);
        $clientScopes = new ClientScopes($config['client_scopes'] ?? [], $config['protected_agents'] ?? [],
                                         $config['protected_workspaces'] ?? []);

        $reqCfg   = (array) ($config['requests'] ?? []);
        $requests = new RequestWatch(new SqliteRequestStore($pdo), new RequestPolicy(array_intersect_key($reqCfg, RequestPolicy::defaults())),
                                     $clock, $ids, $audit, $pdo);
        $submit = new SubmitWorkflow([
            new ReceiveTaskPhase($maxBody),
            new AuthenticateTaskPhase($auth),
            new ValidateTaskPhase(new EnvelopeValidator()),
            new AuthorizeTaskPhase(new PolicyAuthorizer($registry, $config['allowed_operations'], $config['allowed_modes'],
                                                         $config['operation_modes'], $guard, $printGuard, $clientScopes)),
            new GuardRequestDuplicatesPhase($requests, $store),
            new QueueTaskPhase($store, $audit, (int) $config['max_attempts']),
            new LinkRequestPhase($requests),
        ], $audit);

        $client = new ClientTasks($submit, $store, $registry, $audit);
        $uploads = new SqliteUploadStore($pdo, $clock, $ids);
        $notes   = new NotesTools($client, $clientScopes, $audit, $grants, (int) ($config['notes_wait_seconds'] ?? 30), null,
                                  $uploads);
        $fetcher = isset($config['upload_fetcher']) && $config['upload_fetcher'] instanceof IUrlFetcher
                 ? $config['upload_fetcher'] : new CurlUrlFetcher((int) $config['upload_fetch_seconds']);
        $worker = new WorkerTasks($store, $store, $audit, (int) $config['lease_seconds'], (int) $config['max_details'], $clientScopes,
                                  new WorkerPins((array) ($config['worker_pins'] ?? [])));
        $watchCfg = (array) ($config['watch'] ?? []) + ['refresh_seconds' => 30, 'escalate_after' => [900, 3600]];
        $watch    = new AlertWatch(new SqliteWatchSource($pdo), new SqliteWorkerContacts($pdo), new SqliteAlertStore($pdo, $ids),
                                   new QueueWatchPolicy(array_intersect_key($watchCfg, QueueWatchPolicy::defaults())),
                                   new AlertEscalation((array) $watchCfg['escalate_after']), $clock, $audit, $pdo,
                                   (int) $watchCfg['refresh_seconds'], $requests);
        $viewers  = new AlertViewers((array) ($config['alert_viewers'] ?? []), (array) ($config['client_scopes'] ?? []));
        $mcp    = new McpEndpoint($auth, $client, (string) $config['memory_agent'], (string) $config['memory_workspace'],
                                  $settings->challenge(), $grants,
                                  new ScannerTools($client, $store, $scanStore, $blobs, $scopes, $audit,
                                                   in_array('scan', $grants['operations'], true)),
                                  new PrinterTools($client, $printJobs, $printerScopes, $audit, $clock,
                                                   in_array('print', $grants['operations'], true)),
                                  new BrowserTools($client, $clientScopes, $audit, $grants,
                                                   (int) ($config['browser_wait_seconds'] ?? 20), null,
                                                   $downloadStore, $blobs),
                                  new RelayTools($client, $clientScopes, $audit, $grants,
                                                 (int) ($config['relay_wait_seconds'] ?? 45)),
                                  [$notes, new UploadTools($notes, $uploads, $blobs, $audit, $fetcher), new AlertTools($watch, $viewers),
                                   new RequestTools($requests, $viewers)],
                                  new AlertNotice($watch, $viewers, $clock, $requests));
        $scans  = new WorkerScans($store, $store, $scanStore, $blobs, $audit, (int) $config['lease_seconds']);
        return new GatewayApp($auth, $client, $worker, $mcp, new OAuthServer($settings, $oauth, $audit, $clock),
                              $maxBody, $settings->challenge(), (int) $config['max_result_body'], $scans, (int) $config['max_blob'],
                              new WorkerDownloads($store, $store, $downloadStore, $blobs, $audit, (int) $config['lease_seconds']),
                              new WorkerUploads($store, $store, $uploads, $blobs, $audit, (int) $config['lease_seconds']),
                              (int) $config['max_mcp_body'], $watch, $viewers);
    }

    /** Applies each migrations/*.sql once, then seeds the agent registry. Returns applied file names. */
    public static function migrate(PDO $pdo, array $config, string $dir): array
    {
        $pdo->exec('CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at INTEGER NOT NULL)');
        $done    = $pdo->query('SELECT name FROM schema_migrations')->fetchAll(PDO::FETCH_COLUMN);
        $files   = glob(rtrim($dir, '/') . '/*.sql') ?: [];
        sort($files);
        $applied = [];
        foreach ($files as $file) {
            $name = basename($file);
            if (in_array($name, $done, true)) {
                continue;
            }
            Database::immediate($pdo, function () use ($pdo, $file, $name) {
                $pdo->exec((string) file_get_contents($file));
                $pdo->prepare('INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)')->execute([$name, time()]);
            });
            $applied[] = $name;
        }
        SqliteAgentRegistry::seed($pdo, $config['agents']);
        return $applied;
    }
}
