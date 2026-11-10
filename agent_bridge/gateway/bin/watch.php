<?php
declare(strict_types=1);
// Usage: php bin/watch.php [config.php]   (CLI only; e.g. from cron every few minutes)
// Runs the gateway's queue watch once, so alerts are raised and escalated even when
// no client or worker is calling. Prints counts only -- never a task prompt or token.
if (PHP_SAPI !== 'cli') { exit(1); }
require_once __DIR__ . '/../src/Bootstrap.php';

use AgentBridge\Bootstrap;
use AgentBridge\Request;

$config = Bootstrap::loadConfig($argv[1] ?? (getenv('BRIDGE_CONFIG') ?: __DIR__ . '/../config/bridge.php'));
$config['watch'] = ['refresh_seconds' => 0] + (array) ($config['watch'] ?? []);
$app    = Bootstrap::build($config);
$res    = $app->handle(new Request('GET', '/health', [], '', []));
$pdo    = \AgentBridge\Database::open($config['database']);
$open   = (int) $pdo->query('SELECT COUNT(*) FROM alerts WHERE resolved_at IS NULL')->fetchColumn();
$unack  = (int) $pdo->query('SELECT COUNT(*) FROM alerts WHERE resolved_at IS NULL AND acknowledged_at IS NULL')->fetchColumn();
echo "health {$res->status}; open alerts: $open ($unack unacknowledged)\n";
