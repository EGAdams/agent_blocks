#!/usr/bin/env bash

# Runs every check listed in the Event Contracts lesson's section 7. Keep going
# after a failure so one button press always provides the complete picture.
set +e

AGENT_BLOCKS_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
event_contract_failures=0

run_event_contract_check() {
    local label="$1"
    shift

    printf '\n\033[1;36m%s\033[0m\n' "=== ${label} ==="
    "$@"
    local event_contract_status=$?

    if [ "$event_contract_status" -eq 0 ]; then
        printf '\033[1;32mPASS\033[0m %s\n' "$label"
    else
        printf '\033[1;31mFAIL\033[0m %s (exit %s)\n' "$label" "$event_contract_status"
        event_contract_failures=$(( event_contract_failures + 1 ))
    fi
}

cd "$AGENT_BLOCKS_ROOT" || exit 1
printf '\033[1mEvent Contracts checks from section 7\033[0m\n'
printf 'Workspace: %s\n' "$AGENT_BLOCKS_ROOT"

run_event_contract_check \
    "1/4 Read the five kinds and the four readonly fields" \
    sed -n '32,44p' voice_communication/typescript_contracts/contracts.ts

run_event_contract_check \
    "2/4 TypeScript typecheck" \
    bash -c 'cd voice_communication && npm run typecheck'

run_event_contract_check \
    "3/4 The service copy: five kinds, session_id typed UUID" \
    grep -n "class AgentEventKind" -A 6 voice_communication/pydantic_models/models.py

run_event_contract_check \
    "4/4 The adapter copy: two kinds, no session field" \
    grep -n "class AgentEventKind" -A 4 claude_agent_adapter/models.py

printf '\n'
if [ "$event_contract_failures" -eq 0 ]; then
    printf '\033[1;32mAll four section 7 checks passed.\033[0m\n'
else
    printf '\033[1;31m%s of four section 7 checks failed.\033[0m\n' "$event_contract_failures"
fi
printf 'Check 4 is a drift alarm: if it ever prints five kinds, the reconciliation\n'
printf 'has started and the Event Contracts page is out of date.\n'
printf 'The terminal will stay open. You can continue using this Ubuntu shell.\n\n'

cd "$AGENT_BLOCKS_ROOT" || exit 1
exec bash
