#!/usr/bin/env bash

# Runs every check listed in the Characterization Suite lesson's section 6.
# Keep going after a failure so one button press always provides the complete
# picture.
set +e

DOCS_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
browser_test_failures=0

run_browser_test_check() {
    local label="$1"
    shift

    printf '\n\033[1;36m%s\033[0m\n' "=== ${label} ==="
    "$@"
    local browser_test_status=$?

    if [ "$browser_test_status" -eq 0 ]; then
        printf '\033[1;32mPASS\033[0m %s\n' "$label"
    else
        printf '\033[1;31mFAIL\033[0m %s (exit %s)\n' "$label" "$browser_test_status"
        browser_test_failures=$(( browser_test_failures + 1 ))
    fi
}

cd "$DOCS_ROOT" || exit 1
printf '\033[1mCharacterization Suite checks from section 6\033[0m\n'
printf 'Workspace: %s\n' "$DOCS_ROOT"

run_browser_test_check \
    "1/5 Read the abstract factory" \
    cat browser_tests/platform-profile/platform-profile.ts

run_browser_test_check \
    "2/5 The composition root is the only module naming concrete classes" \
    bash -c "grep -rln 'create\\(Windows\\|Linux\\)PlatformProfile\\|createDialogStepGate\\|createBannerHoldOpenNotice' browser_tests/ | grep -v '^browser_tests/composition-root/' | grep -v -- '-profile/\\|dialog-step-gate/\\|banner-hold-open-notice/' && echo 'LEAK: a module outside the composition root selects an implementation' && exit 1 || echo 'No leaks: only composition-root selects implementations'"

run_browser_test_check \
    "3/5 No test names a raw Playwright selector" \
    bash -c "grep -n 'page\\.locator\\|getByRole' browser_tests/*.test.ts && echo 'LEAK: a raw selector escaped the page object' && exit 1 || echo 'Clean: legacy-navigation.test.ts speaks only SpaPage'"

run_browser_test_check \
    "4/5 Strict typecheck of app, app_tests and browser_tests" \
    npm run typecheck

run_browser_test_check \
    "5/5 The six characterization tests" \
    npm test

printf '\n'
if [ "$browser_test_failures" -eq 0 ]; then
    printf '\033[1;32mAll five section 6 checks passed.\033[0m\n'
else
    printf '\033[1;31m%s of five section 6 checks failed.\033[0m\n' "$browser_test_failures"
fi
printf 'Check 5 needs Chrome at /usr/bin/google-chrome on a Linux host. That path\n'
printf 'is hard-coded in browser_tests/linux-platform-profile/linux-platform-profile.ts\n'
printf 'with no environment override -- see section 5 of the lesson.\n'
printf 'The terminal will stay open. You can continue using this Ubuntu shell.\n\n'

cd "$DOCS_ROOT" || exit 1
exec bash
