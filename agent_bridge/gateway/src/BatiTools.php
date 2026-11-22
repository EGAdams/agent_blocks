<?php
declare(strict_types=1);

namespace AgentBridge;

require_once __DIR__ . '/AgentTaskTools.php';

/**
 * The bati_* MCP tools: the debug Chrome on BATI's own Windows desktop (DESKTOP-SHDBATI), through the Bridge
 * `bati-desktop` agent, which BATI's own machine-lane worker runs (worker_pins offers it to no other worker) over
 * adws/adw_bati_chrome.py. Read adws/bati_desktop/DESIGN.md.
 *
 *   inspect    read_only        bati_status, bati_read, bati_screenshot
 *   implement  workspace_write  bati_open, bati_show_view, bati_plan_for_today
 *
 * The gateway writes `machine: "BATI"` into every prompt itself, so a caller can never aim one of these at another
 * machine. The URL and view allowlists are checked here first (the dashboard origin, the Plan for Today view), and
 * again by the worker's ADW, which is what actually decides. There is no tool that types, fills, clicks a form,
 * closes a tab or quits Chrome, because there is no such operation anywhere in the chain.
 */
final class BatiTools extends AgentTaskTools
{
    const AGENT     = 'bati-desktop';
    const WORKSPACE = 'bati-desktop';
    const MACHINE   = 'BATI';

    const ORIGINS = ['http://localhost:8765', 'http://127.0.0.1:8765'];
    const VIEWS   = ['plan-for-today'];
    const MAX_URL = 300;

    /** tool => [worker operation, task operation, allowed fields, required fields]. */
    const TOOLS = [
        'bati_status'         => ['status', 'inspect', [], []],
        'bati_read'           => ['read', 'inspect', ['view'], []],
        'bati_screenshot'     => ['screenshot', 'inspect', [], []],
        'bati_open'           => ['open', 'implement', ['url'], []],
        'bati_show_view'      => ['show_view', 'implement', ['view'], []],
        'bati_plan_for_today' => ['plan_for_today', 'implement', ['url'], []],
        'bati_get_result'     => [null, 'inspect', [], []],
    ];

    const DETAIL_KEYS = ['bati', 'cause', 'adw_id', 'machine', 'actor', 'started', 'finished', 'data'];

    /** What to say, by the cause BATI reported. Never a way round. */
    const NEXT = [
        'wrong_machine'      => 'The task did not run on BATI, so nothing was done. Tell EG; do not send it anywhere else.',
        'url_not_allowed'    => 'BATI\'s Chrome only opens the dashboard (http://localhost:8765). Nothing was opened.',
        'view_not_allowed'   => 'The only dashboard view that may be shown on BATI is the Plan for Today.',
        'hidden_session'     => 'The debug Chrome is not on a desktop anyone can see. Tell EG; nothing more can be done from here.',
        'no_desktop'         => 'Nobody is signed in to BATI\'s Windows desktop. Ask EG to sign in on BATI, then ask again.',
        'visibility_unknown' => 'Windows did not say where Chrome is, so the step stopped. Ask once more; if it repeats, tell EG.',
        'no_dashboard_tab'   => 'The debug Chrome has no dashboard tab. Call bati_open first.',
        'launch'             => 'Chrome could not be started on BATI\'s desktop. Tell EG what the line says.',
        'control_port'       => 'Chrome started but its control port never answered. Tell EG.',
        'view_not_shown'     => 'The tab was pressed but the view did not appear. Ask once more; if it repeats, tell EG.',
        'no_outcome'         => 'The BATI Chrome workflow gave no result. Tell EG what the line says.',
    ];

    public function __construct(ClientTasks $client, ClientScopes $scopes, IAuditLogger $audit, array $grants,
                                int $waitSeconds = 60, ?callable $sleep = null)
    {
        parent::__construct($client, $scopes, $audit, $grants, $waitSeconds, $sleep);
    }

    protected function agent(): string { return self::AGENT; }
    protected function workspace(): string { return self::WORKSPACE; }
    protected function tools(): array { return self::TOOLS; }
    protected function detailKeys(): array { return self::DETAIL_KEYS; }

    public function instructions(): string
    {
        return 'The bati_* tools drive the debug Chrome on BATI, EG\'s own Windows computer (DESKTOP-SHDBATI), and '
             . 'nothing else: never use browser_* (that is another machine\'s Chrome) for BATI. "Is Chrome up on '
             . 'BATI" -> bati_status. "Show the Plan for Today on BATI" -> bati_plan_for_today, which opens the '
             . 'dashboard, switches to the Plan for Today, reads it back and checks today\'s date is in its heading. '
             . '"What is showing on BATI" -> bati_read. These tools never type, fill a form, close a tab or quit '
             . 'Chrome; say so if asked. If verified is false, tell EG what next says and stop.';
    }

    protected function requestFor(string $op, array $args, array $allowed, array $required): array
    {
        $out = ['bati' => $op, 'machine' => self::MACHINE];
        if (in_array('url', $allowed, true) && isset($args['url']) && $args['url'] !== '') {
            $out['url'] = self::allowedUrl($args['url']);
        }
        if (in_array('view', $allowed, true)) {
            $view = isset($args['view']) && $args['view'] !== '' ? self::allowedView($args['view']) : '';
            if ($view === '' && $op === 'show_view') {
                $view = self::VIEWS[0];
            }
            if ($view !== '') {
                $out['view'] = $view;
            }
        }
        return $out;
    }

    private static function allowedUrl($url): string
    {
        if (!is_string($url) || mb_strlen($url) > self::MAX_URL || $url[0] === '-') {
            throw new BridgeError(422, 'invalid_argument', 'url must be a dashboard address of at most ' . self::MAX_URL
                . ' characters');
        }
        $u = parse_url($url);
        if (!is_array($u) || isset($u['user']) || isset($u['pass']) || !isset($u['scheme'], $u['host'])) {
            throw new BridgeError(422, 'invalid_argument', 'url must be a plain dashboard address, with no credentials');
        }
        $origin = strtolower($u['scheme']) . '://' . strtolower($u['host']) . (isset($u['port']) ? ':' . $u['port'] : '');
        if (!in_array($origin, self::ORIGINS, true)) {
            throw new BridgeError(422, 'invalid_argument', 'BATI\'s Chrome only opens the dashboard: '
                . implode(' or ', self::ORIGINS));
        }
        return $url;
    }

    private static function allowedView($view): string
    {
        if (!is_string($view) || mb_strlen($view) > 60) {
            throw new BridgeError(422, 'invalid_argument', 'view must be a dashboard view name');
        }
        $name = strtolower(trim((string) preg_replace('/[\s_]+/', '-', trim($view)), '-'));
        if (!in_array($name, self::VIEWS, true)) {
            throw new BridgeError(422, 'invalid_argument', 'the only view that may be shown on BATI is '
                . implode(', ', self::VIEWS));
        }
        return $name;
    }

    protected function pending(): string
    {
        return 'BATI is still working on it (starting Chrome takes longest). Call bati_get_result with this task_id; '
             . 'do not ask again, it would only queue a second task.';
    }

    protected function notDone(array $details): string
    {
        $cause = isset($details['cause']) ? (string) $details['cause'] : '';
        if (isset(self::NEXT[$cause])) {
            return self::NEXT[$cause];
        }
        return 'Not done on BATI. Read the line, tell EG what it says, and do not try another machine or tool.';
    }

    protected function definitionTable(): array
    {
        $read  = ['readOnlyHint' => true, 'destructiveHint' => false, 'openWorldHint' => false];
        $step  = ['readOnlyHint' => false, 'destructiveHint' => false, 'openWorldHint' => false];
        $url   = ['url' => ['type' => 'string', 'maxLength' => self::MAX_URL,
                            'description' => 'The dashboard address; default http://localhost:8765/. No other site is allowed.']];
        $view  = ['view' => ['type' => 'string', 'enum' => self::VIEWS,
                             'description' => 'The dashboard view (only plan-for-today)']];
        $after = ' Runs on BATI only. Waits up to about a minute; if ready=false, call bati_get_result with the task_id '
               . 'rather than asking again.';
        $never = ' Never types, fills a form, closes a tab or quits Chrome.';
        return [
            'bati_status' => ['title' => 'Is the debug Chrome up on BATI',
                'description' => 'Whether the debug Chrome is running on BATI\'s Windows desktop, which signed-in desktop '
                               . 'it is on, and its open tabs (addresses without query strings).' . $after,
                'inputSchema' => self::schema([], []), 'annotations' => $read],
            'bati_read' => ['title' => 'What the dashboard on BATI is showing',
                'description' => 'The dashboard tab\'s address, title, which view is showing, its heading, status line '
                               . 'and task count. No form values, cookies or storage.' . $after,
                'inputSchema' => self::schema($view, []), 'annotations' => $read],
            'bati_screenshot' => ['title' => 'Photograph the dashboard tab on BATI',
                'description' => 'Saves a screenshot of the dashboard tab on BATI (its path comes back in data); the '
                               . 'picture stays on BATI.' . $after,
                'inputSchema' => self::schema([], []), 'annotations' => $read],
            'bati_open' => ['title' => 'Open the dashboard in BATI\'s Chrome',
                'description' => 'Starts the debug Chrome on BATI\'s desktop if it is not up, and opens or reuses one '
                               . 'dashboard tab.' . $never . $after,
                'inputSchema' => self::schema($url, []), 'annotations' => $step],
            'bati_show_view' => ['title' => 'Switch BATI\'s dashboard to the Plan for Today',
                'description' => 'Presses the dashboard\'s Plan for Today tab on BATI until that view is showing.'
                               . $never . $after,
                'inputSchema' => self::schema($view, []), 'annotations' => $step],
            'bati_plan_for_today' => ['title' => 'Show the Plan for Today on BATI',
                'description' => 'Open the dashboard on BATI, show the Plan for Today, read it back and photograph it; '
                               . 'verified only if today\'s date is in its heading in a visible tab.' . $never . $after,
                'inputSchema' => self::schema($url, []), 'annotations' => $step],
            'bati_get_result' => ['title' => 'The answer to a bati_* request that was still running',
                'description' => 'The result of an earlier bati_* call that returned ready=false.',
                'inputSchema' => self::schema(['task_id' => ['type' => 'string', 'description' => 'The task_id that call gave']],
                                              ['task_id']), 'annotations' => $read],
        ];
    }
}
