<?php
declare(strict_types=1);
// Usage: php bin/issue_token.php <client|worker> <label> [config.php]
//        php bin/issue_token.php --revoke <token-id> [config.php]
// The token is printed once and stored only as a sha256 hash.
if (PHP_SAPI !== 'cli') { exit(1); }
require_once __DIR__ . '/../src/Bootstrap.php';

use AgentBridge\Bootstrap;
use AgentBridge\Database;
use AgentBridge\RandomIds;
use AgentBridge\SystemClock;
use AgentBridge\TokenAuthenticator;

if (count($argv) < 3) {
    fwrite(STDERR, "usage: issue_token.php <client|worker> <label> | --revoke <id>\n");
    exit(2);
}
$config = Bootstrap::loadConfig($argv[3] ?? (getenv('BRIDGE_CONFIG') ?: __DIR__ . '/../config/bridge.php'));
$pdo    = Database::open($config['database']);
if ($argv[1] === '--revoke') {
    $stmt = $pdo->prepare('UPDATE api_tokens SET revoked = 1 WHERE id = ?');
    $stmt->execute([$argv[2]]);
    echo $stmt->rowCount() === 1 ? "revoked {$argv[2]}\n" : "no such token\n";
    exit($stmt->rowCount() === 1 ? 0 : 1);
}
$issued = TokenAuthenticator::issue($pdo, new RandomIds(), new SystemClock(), $argv[1], substr($argv[2], 0, 80));
echo "id:    {$issued['id']}\n";
echo "token: {$issued['token']}\n";
echo "Store the token now; it cannot be shown again.\n";
