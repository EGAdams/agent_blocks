<?php
declare(strict_types=1);
// Usage: php bin/oauth_admin.php passphrase [config.php]   reads the new owner passphrase from stdin
//        php bin/oauth_admin.php grants [config.php]       lists ChatGPT connections
//        php bin/oauth_admin.php revoke <grant-id> [config.php]
// The passphrase is read from stdin, never argv, so it stays out of the process list and shell history.
if (PHP_SAPI !== 'cli') { exit(1); }
require_once __DIR__ . '/../src/Bootstrap.php';

use AgentBridge\Bootstrap;
use AgentBridge\Database;
use AgentBridge\OAuthStore;
use AgentBridge\RandomIds;
use AgentBridge\SystemClock;

$command = $argv[1] ?? '';
$rest    = array_slice($argv, $command === 'revoke' ? 3 : 2);
$config  = Bootstrap::loadConfig($rest[0] ?? (getenv('BRIDGE_CONFIG') ?: __DIR__ . '/../config/bridge.php'));
$pdo     = Database::open($config['database']);
$store   = new OAuthStore($pdo, new SystemClock(), new RandomIds());

switch ($command) {
    case 'passphrase':
        $passphrase = rtrim((string) fgets(STDIN), "\r\n");
        if (strlen($passphrase) < 12) {
            fwrite(STDERR, "the passphrase must be at least 12 characters\n");
            exit(2);
        }
        OAuthStore::setPassphrase($pdo, time(), $passphrase);
        echo "owner passphrase set\n";
        exit(0);
    case 'grants':
        foreach ($store->grants() as $g) {
            printf("%s  %s  %s%s\n", $g['id'], gmdate('Y-m-d H:i', (int) $g['created_at']), $g['name'], $g['revoked'] ? '  (revoked)' : '');
        }
        exit(0);
    case 'revoke':
        $ok = $store->revokeGrant($argv[2] ?? '');
        echo $ok ? "revoked\n" : "no such live grant\n";
        exit($ok ? 0 : 1);
    default:
        fwrite(STDERR, "usage: oauth_admin.php passphrase | grants | revoke <grant-id>\n");
        exit(2);
}
