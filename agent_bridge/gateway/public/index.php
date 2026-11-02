<?php
declare(strict_types=1);

// Front controller. Holds no logic; the config (with the database path) lives
// outside the web root and is named by BRIDGE_CONFIG or sits at ../config.
require_once __DIR__ . '/../src/Bootstrap.php';

use AgentBridge\Bootstrap;
use AgentBridge\Request;
use AgentBridge\Response;

$configPath = getenv('BRIDGE_CONFIG') ?: __DIR__ . '/../config/bridge.php';
try {
    $config = Bootstrap::loadConfig($configPath);
    $app    = Bootstrap::build($config);
    $app->handle(Request::fromGlobals('/api/agent-bridge/v1', max((int) $config['max_body'], (int) $config['max_result_body'], (int) $config['max_blob'])))->emit();
} catch (\Throwable $e) {
    error_log('agent-bridge bootstrap: ' . get_class($e));
    Response::error(503, 'unavailable', 'gateway is not configured')->emit();
}
