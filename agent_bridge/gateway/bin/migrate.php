<?php
declare(strict_types=1);
// Usage: php bin/migrate.php [config.php]   (CLI only)
if (PHP_SAPI !== 'cli') { exit(1); }
require_once __DIR__ . '/../src/Bootstrap.php';

use AgentBridge\Bootstrap;
use AgentBridge\Database;

$config = Bootstrap::loadConfig($argv[1] ?? (getenv('BRIDGE_CONFIG') ?: __DIR__ . '/../config/bridge.php'));
$dir    = dirname($config['database']);
if (!is_dir($dir) && !mkdir($dir, 0700, true)) {
    fwrite(STDERR, "cannot create database directory\n");
    exit(1);
}
$pdo     = Database::open($config['database']);
$applied = Bootstrap::migrate($pdo, $config, __DIR__ . '/../migrations');
@chmod($config['database'], 0600);
echo 'applied: ' . ($applied ? implode(', ', $applied) : 'none (up to date)') . "\n";
echo 'agents: ' . count($config['agents']) . " seeded\n";
