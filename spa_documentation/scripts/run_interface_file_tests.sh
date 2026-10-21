#!/usr/bin/env bash

# Runs every check listed in the Interface File lesson's section 6. Keep going
# after a failure so one button press always provides the complete picture.
set +e

AGENT_BLOCKS_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
interface_test_failures=0

run_interface_check() {
    local label="$1"
    shift

    printf '\n\033[1;36m%s\033[0m\n' "=== ${label} ==="
    "$@"
    local interface_test_status=$?

    if [ "$interface_test_status" -eq 0 ]; then
        printf '\033[1;32mPASS\033[0m %s\n' "$label"
    else
        printf '\033[1;31mFAIL\033[0m %s (exit %s)\n' "$label" "$interface_test_status"
        interface_test_failures=$(( interface_test_failures + 1 ))
    fi
}

cd "$AGENT_BLOCKS_ROOT" || exit 1
printf '\033[1mInterface File checks from section 6\033[0m\n'
printf 'Workspace: %s\n' "$AGENT_BLOCKS_ROOT"

run_interface_check \
    "1/4 Read the nine-line interface" \
    cat voice_communication/conversation_agent/conversation_agent.ts

run_interface_check \
    "2/4 TypeScript typecheck" \
    bash -c 'cd voice_communication && npm run typecheck'

run_interface_check \
    "3/4 Locate IConversationAgent references" \
    grep -rn "IConversationAgent" voice_communication/

run_interface_check \
    "4/4 Claude adapter tests" \
    bash -c 'cd claude_agent_adapter && ../lancedb_memory/.venv/bin/python tests/test_adapter.py'

printf '\n'
if [ "$interface_test_failures" -eq 0 ]; then
    printf '\033[1;32mAll four section 6 checks passed.\033[0m\n'
else
    printf '\033[1;31m%s of four section 6 checks failed.\033[0m\n' "$interface_test_failures"
fi
printf 'The terminal will stay open. You can continue using this Ubuntu shell.\n\n'

cd "$AGENT_BLOCKS_ROOT" || exit 1
exec bash
