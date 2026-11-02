<?php
// Copy to bridge.php OUTSIDE the web root and point BRIDGE_CONFIG at it.
// No secrets live here: tokens are issued by bin/issue_token.php and stored hashed.
return [
    'database'           => '/home1/tinman72/agent_bridge_data/bridge.sqlite',
    // Where ChatGPT reaches this gateway; OAuth metadata and token audiences are built from these.
    'public_base'        => 'https://bridge.americansjewelry.com',
    'api_prefix'         => '/api/agent-bridge/v1',
    'lease_seconds'      => 120,
    'max_attempts'       => 2,
    'max_body'           => 65536,
    'max_result_body'    => 262144,   // the worker's complete report only; everything else keeps max_body
    // Server-side policy. Each operation runs in one mode; a client cannot pick another.
    'operation_modes'    => ['inspect' => 'read_only', 'explain' => 'read_only', 'search_memory' => 'read_only',
                             'implement' => 'workspace_write'],
    // Read-only. To switch scoped writes ON, add 'implement' here and 'workspace_write'
    // to allowed_modes; to switch them OFF again, take both out. Nothing else changes.
    'allowed_operations' => ['inspect', 'explain', 'search_memory'],
    'allowed_modes'      => ['read_only'],
    'memory_agent'       => 'codex',
    'memory_workspace'   => 'agent_blocks',
    'agents' => [
        ['name' => 'codex', 'display_name' => 'Codex', 'description' => 'Codex CLI in a read-only sandbox: inspects and explains code.',
         'operations' => ['inspect', 'explain', 'search_memory'], 'workspaces' => ['agent_blocks']],
        ['name' => 'hello-world', 'display_name' => 'Hello World', 'description' => 'Echo agent for round-trip tests.',
         'operations' => ['inspect'], 'workspaces' => ['agent_blocks']],
        // Writes only where write_workspaces says, and only when implement is allowed above.
        ['name' => 'codex-writer', 'display_name' => 'Codex Writer',
         'description' => 'Codex CLI in a workspace-write sandbox: creates and edits files in the bridge_sandbox test workspace only.',
         'operations' => ['implement'], 'workspaces' => ['bridge_sandbox'], 'write_workspaces' => ['bridge_sandbox']],
    ],
];
