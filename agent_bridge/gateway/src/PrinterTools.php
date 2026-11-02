<?php
declare(strict_types=1);

namespace AgentBridge;

/**
 * The printer_* MCP tools. Each is listed only to a caller holding its scope,
 * and every one that reaches the machine goes through ClientTasks->submit, so
 * PolicyAuthorizer and PrintGuard see it exactly as they see a REST task.
 *
 * Only printer_print_text and printer_print_document can print, and only with
 * confirm=true. Every read tool answers from what the gateway already holds,
 * or queues a read-only discover/status task: none of them can print. Nothing
 * here claims paper came out -- physical_output_verified is always false.
 * Audit records carry ids and states, never the text being printed.
 */
final class PrinterTools
{
    const TOOLS = [
        'printer_list'           => 'printer:list',
        'printer_get_default'    => 'printer:list',
        'printer_list_documents' => 'printer:list',
        'printer_print_text'     => 'printer:print',
        'printer_print_document' => 'printer:print',
        'printer_get_status'     => 'printer:status',
        'printer_list_recent'    => 'printer:status',
        'printer_cancel'         => 'printer:cancel',
    ];
    const PRINTS       = ['printer_print_text', 'printer_print_document', 'printer_cancel'];
    const DISCOVER_TTL = 600;
    const MAX_PROMPT   = 8000;
    const NOT_PAPER    = 'Status comes from the Windows print queue. No printer here confirms that paper came out; '
                       . 'completed_per_spooler means Windows finished with the job, not that EG holds the page.';

    /** @var ClientTasks */   private $client;
    /** @var IPrinterJobs */  private $jobs;
    /** @var PrinterScopes */ private $scopes;
    /** @var IAuditLogger */  private $audit;
    /** @var IClock */        private $clock;
    /** @var bool print and print_cancel are in allowed_operations and device_control is granted */ private $printEnabled;

    public function __construct(ClientTasks $client, IPrinterJobs $jobs, PrinterScopes $scopes, IAuditLogger $audit,
                                IClock $clock, bool $printEnabled)
    {
        $this->client       = $client;
        $this->jobs         = $jobs;
        $this->scopes       = $scopes;
        $this->audit        = $audit;
        $this->clock        = $clock;
        $this->printEnabled = $printEnabled;
    }

    public function handles(string $name): bool
    {
        return isset(self::TOOLS[$name]);
    }

    public function offered(Principal $who): bool
    {
        return (bool) $this->definitions($who);
    }

    public function definitions(Principal $who): array
    {
        $out = [];
        foreach (self::definitionTable() as $name => $tool) {
            if ($this->permitted($who, $name)) {
                $out[] = ['name' => $name] + $tool;
            }
        }
        return $out;
    }

    public function call(Request $r, Principal $who, string $name, array $args): array
    {
        if (!$this->permitted($who, $name)) {
            $this->audit->record($who->id, $name, null, 'denied');
            return self::error('insufficient_scope: this client may not use ' . $name);
        }
        try {
            switch ($name) {
                case 'printer_list':           return self::ok($this->discovered($r, $who, $args, 'printers'));
                case 'printer_get_default':    return self::ok($this->discovered($r, $who, $args, 'default'));
                case 'printer_list_documents': return self::ok($this->discovered($r, $who, $args, 'documents'));
                case 'printer_print_text':     return self::ok($this->printText($r, $who, $args));
                case 'printer_print_document': return self::ok($this->printDocument($r, $who, $args));
                case 'printer_get_status':     return self::ok($this->status($r, $who, $args));
                case 'printer_list_recent':    return self::ok($this->recent($who, $args));
                default:                       return self::ok($this->cancel($r, $who, $args));
            }
        } catch (BridgeError $e) {
            $this->audit->record($who->id, $name, null, $e->errorCode);
            return self::error($e->errorCode . ': ' . $e->getMessage());
        }
    }

    private function permitted(Principal $who, string $name): bool
    {
        if (!isset(self::TOOLS[$name]) || !$this->scopes->allows($who, self::TOOLS[$name])) {
            return false;
        }
        return !in_array($name, self::PRINTS, true) || $this->printEnabled;
    }

    // ---- discovery: the cached answer, or a read-only discover task ---------------------

    private function discovered(Request $r, Principal $who, array $args, string $what): array
    {
        $refresh = isset($args['refresh']) && $args['refresh'] === true;
        $latest  = $this->jobs->latest($who->id, 'print_discover');
        $fresh   = $latest !== null && $latest['state'] === TaskState::SUCCEEDED
                && $this->clock->now() - (int) $latest['created_at'] <= self::DISCOVER_TTL;
        if ($fresh && !$refresh) {
            $details = self::details($latest);
            $out     = ['ready' => true, 'checked_at' => (int) $latest['created_at'], 'task_id' => $latest['id']];
            if ($what === 'printers') {
                $out += ['printers' => self::printers($details), 'default' => self::defaultId($details)];
            } elseif ($what === 'default') {
                $out += ['default' => self::defaultId($details), 'printer' => self::defaultPrinter($details)];
            } else {
                $out += ['documents' => self::documents($details)];
            }
            $this->audit->record($who->id, 'printer_' . $what, $latest['id'], 'ok');
            return $out;
        }
        if ($latest !== null && in_array($latest['state'], [TaskState::QUEUED, TaskState::RUNNING], true)) {
            return ['ready' => false, 'task_id' => $latest['id'], 'state' => $latest['state'],
                    'next' => 'EG\'s machine is already asking Windows; call this again in a few seconds.'];
        }
        $view = $this->submit($r, 'print_discover', 'read_only', 'discover');
        return ['ready' => false, 'task_id' => $view['task_id'], 'state' => $view['state'],
                'next' => 'Asked EG\'s machine which printers Windows has. This reads only: nothing is installed, '
                        . 'changed or printed. Call this again in a few seconds.'];
    }

    // ---- printing: confirm=true, a JSON prompt built here, never a path -------------------

    private function printText(Request $r, Principal $who, array $args): array
    {
        self::confirmed($args);
        $test = isset($args['test_page']) && $args['test_page'] === true;
        $req  = $test ? ['kind' => 'test_page'] : ['kind' => 'text', 'text' => self::str($args, 'text')];
        return $this->print($r, $who, $args, $req + self::common($args, !$test));
    }

    private function printDocument(Request $r, Principal $who, array $args): array
    {
        self::confirmed($args);
        return $this->print($r, $who, $args, ['kind' => 'document', 'document_id' => self::str($args, 'document_id')]
                                             + self::common($args, true));
    }

    private function print(Request $r, Principal $who, array $args, array $req): array
    {
        $prompt = (string) json_encode($req, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
        if (mb_strlen($prompt) > self::MAX_PROMPT) {
            throw new BridgeError(422, 'too_large', 'that print is too long for one bridge task; shorten the text');
        }
        $problem = PrintPromptShape::problem('print', $prompt);
        if ($problem !== null) {
            throw new BridgeError(422, 'invalid_print', $problem);
        }
        $key  = isset($args['idempotency_key']) ? (is_string($args['idempotency_key']) ? $args['idempotency_key'] : '') : null;
        $view = $this->submit($r, 'print', 'device_control', $prompt, $key);
        $this->audit->record($who->id, 'printer_print', $view['task_id'], $view['state'],
                             ['kind' => $req['kind'], 'replayed' => $view['replayed']]);
        return ['job_id' => $view['task_id'], 'state' => 'accepted', 'task_state' => $view['state'],
                'replayed' => $view['replayed'], 'physical_output_verified' => false,
                'next' => 'Accepted by the gateway; EG\'s machine prints it on its next poll. Follow it with printer_get_status. '
                        . self::NOT_PAPER];
    }

    /** title, printer_id and the options, copied only when present and of the right type. */
    private static function common(array $args, bool $withTitle): array
    {
        $out = [];
        if ($withTitle && isset($args['title'])) {
            $out['title'] = self::str($args, 'title');
        }
        if (isset($args['printer_id'])) {
            $out['printer_id'] = self::str($args, 'printer_id');
        }
        $options = [];
        foreach (['copies', 'page_range', 'orientation'] as $key) {
            if (isset($args[$key])) {
                $options[$key] = $args[$key];
            }
        }
        return $options ? $out + ['options' => $options] : $out;
    }

    // ---- status, recent, cancel ------------------------------------------------------

    private function status(Request $r, Principal $who, array $args): array
    {
        $task    = $this->printTask($who, $args);
        $details = self::details($task);
        $checked = $this->jobs->latest($who->id, 'print_status', 'job=' . $task['id']);
        $checkedJob = $checked !== null ? self::job(self::details($checked)) : null;
        $job     = $checkedJob ?: self::job($details);
        $refresh = isset($args['refresh']) && $args['refresh'] === true;
        $pending = $checked !== null && in_array($checked['state'], [TaskState::QUEUED, TaskState::RUNNING], true);
        $queuedCheck = null;
        if ($refresh && !$pending && $task['state'] !== TaskState::QUEUED && $task['state'] !== TaskState::CANCELLED) {
            $queuedCheck = $this->submit($r, 'print_status', 'read_only', 'job=' . $task['id'])['task_id'];
        }
        $this->audit->record($who->id, 'printer_get_status', $task['id'], 'ok');
        return self::jobView($task, $job) + [
            'checking' => $queuedCheck !== null || $pending,
            'check_task_id' => $queuedCheck ?: ($pending ? $checked['id'] : null),
            'note' => self::NOT_PAPER,
        ];
    }

    private function recent(Principal $who, array $args): array
    {
        $limit = isset($args['limit']) && is_int($args['limit']) ? max(1, min(20, $args['limit'])) : 10;
        $rows  = $this->jobs->recentPrints($who->id, $limit);
        $this->audit->record($who->id, 'printer_list_recent', null, 'ok', ['count' => count($rows)]);
        return ['jobs' => array_map(function ($t) {
                    return self::jobView($t, self::job(self::details($t)));
                }, $rows), 'note' => self::NOT_PAPER];
    }

    private function cancel(Request $r, Principal $who, array $args): array
    {
        self::confirmed($args);
        $task = $this->printTask($who, $args);
        if ($task['state'] === TaskState::QUEUED) {
            $view = $this->client->cancel($who, $task['id']);
            return ['job_id' => $task['id'], 'state' => $view['state'] === TaskState::CANCELLED ? 'canceled' : $view['state'],
                    'detail' => 'Cancelled before EG\'s machine picked it up; nothing was sent to a printer.'];
        }
        if (in_array($task['state'], [TaskState::FAILED, TaskState::CANCELLED], true)) {
            throw new BridgeError(409, 'not_cancelable', 'job ' . $task['id'] . ' already ended as ' . $task['state']);
        }
        $view = $this->submit($r, 'print_cancel', 'device_control', 'cancel=' . $task['id'], 'cancel:' . $task['id']);
        $this->audit->record($who->id, 'printer_cancel', $task['id'], $view['state']);
        return ['job_id' => $task['id'], 'state' => 'cancel_requested', 'cancel_task_id' => $view['task_id'],
                'replayed' => $view['replayed'],
                'detail' => 'Asked EG\'s machine to remove the job from the Windows queue. Pages already printing may still '
                          . 'come out. Check printer_get_status.'];
    }

    private function printTask(Principal $who, array $args): array
    {
        $task = $this->jobs->printJob($who->id, self::str($args, 'job_id'));
        if ($task === null) {
            throw new BridgeError(404, 'not_found', 'no such print job');
        }
        return $task;
    }

    // ---- shared plumbing ------------------------------------------------------------

    private function submit(Request $r, string $operation, string $mode, string $prompt, ?string $key = null): array
    {
        $envelope = ['schema_version' => '1.0', 'task_type' => 'agent_task', 'agent' => 'printer', 'workspace' => 'printers',
                     'operation' => $operation, 'approval_mode' => $mode, 'prompt' => $prompt];
        if ($key !== null) {
            $envelope['idempotency_key'] = $key;
        }
        $inner = new Request('POST', '/tasks', ['authorization' => $r->header('authorization')], (string) json_encode($envelope));
        list(, $view) = $this->client->submit($inner);
        return $view;
    }

    /** The client's view of one print: the gateway's task state, and the printer's own state when it reported one. */
    private static function jobView(array $task, ?array $job): array
    {
        $req = json_decode((string) $task['prompt'], true);
        $req = is_array($req) ? $req : [];
        return ['job_id' => $task['id'], 'task_state' => $task['state'],
                'state' => $job !== null && isset($job['state']) ? (string) $job['state'] : self::taskOnlyState($task),
                'kind' => isset($req['kind']) ? $req['kind'] : null,
                'title' => isset($job['title']) ? self::clip((string) $job['title'], 120)
                         : (isset($req['title']) ? self::clip((string) $req['title'], 120) : null),
                'printer' => isset($job['printer']) ? $job['printer'] : (isset($req['printer_id']) ? $req['printer_id'] : 'default'),
                'detail' => isset($job['detail']) ? self::clip((string) $job['detail'], 300)
                          : ($task['last_error'] !== null ? self::clip((string) $task['last_error'], 300) : null),
                'created_at' => (int) $task['created_at'],
                'physical_output_verified' => false];
    }

    private static function taskOnlyState(array $task): string
    {
        $map = [TaskState::QUEUED => 'queued', TaskState::RUNNING => 'submitting', TaskState::CANCELLED => 'canceled',
                TaskState::FAILED => 'failed'];
        return isset($map[$task['state']]) ? $map[$task['state']] : 'unknown';
    }

    private static function details(array $row): array
    {
        $d = isset($row['result_details']) ? json_decode((string) $row['result_details'], true) : null;
        return is_array($d) ? $d : [];
    }

    private static function job(array $details): ?array
    {
        return isset($details['job']) && is_array($details['job']) ? $details['job'] : null;
    }

    private static function printers(array $d): array
    {
        $list = isset($d['printers']) && is_array($d['printers']) ? $d['printers'] : [];
        return array_values(array_map(function ($p) {
            return ['printer_id' => isset($p['printer_id']) ? (string) $p['printer_id'] : '',
                    'name' => isset($p['name']) ? self::clip((string) $p['name'], 120) : '',
                    'status' => isset($p['status']) ? (string) $p['status'] : 'unknown',
                    'is_default' => !empty($p['is_default'])];
        }, array_filter($list, 'is_array')));
    }

    private static function defaultId(array $d): ?string
    {
        return isset($d['default']) && is_string($d['default']) ? $d['default'] : null;
    }

    private static function defaultPrinter(array $d): ?array
    {
        foreach (self::printers($d) as $p) {
            if ($p['printer_id'] === self::defaultId($d)) {
                return $p;
            }
        }
        return null;
    }

    private static function documents(array $d): array
    {
        $list = isset($d['documents']) && is_array($d['documents']) ? $d['documents'] : [];
        return array_values(array_map(function ($doc) {
            return ['document_id' => isset($doc['document_id']) ? (string) $doc['document_id'] : '',
                    'name' => isset($doc['name']) ? self::clip((string) $doc['name'], 120) : '',
                    'mime' => isset($doc['mime']) ? (string) $doc['mime'] : '',
                    'bytes' => isset($doc['bytes']) ? (int) $doc['bytes'] : 0,
                    'pages' => isset($doc['pages']) ? $doc['pages'] : null];
        }, array_filter($list, 'is_array')));
    }

    private static function confirmed(array $args): void
    {
        if (!isset($args['confirm']) || $args['confirm'] !== true) {
            throw new BridgeError(422, 'confirmation_required',
                                  'printing and cancelling need confirm=true, and only when EG asked for it; nothing was sent');
        }
    }

    private static function str(array $args, string $key): string
    {
        return isset($args[$key]) && is_string($args[$key]) ? $args[$key] : '';
    }

    private static function clip(string $text, int $max): string
    {
        return mb_strlen($text) <= $max ? $text : mb_substr($text, 0, $max);
    }

    private static function ok(array $out): array
    {
        return ['content' => [['type' => 'text', 'text' => json_encode($out, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE)]],
                'structuredContent' => $out, 'isError' => false];
    }

    private static function error(string $text): array
    {
        return ['content' => [['type' => 'text', 'text' => $text]], 'isError' => true];
    }

    private static function definitionTable(): array
    {
        $read   = ['readOnlyHint' => true, 'destructiveHint' => false, 'openWorldHint' => false];
        $prints = ['readOnlyHint' => false, 'destructiveHint' => false, 'idempotentHint' => true, 'openWorldHint' => true];
        $object = function (array $props, array $required) {
            return ['type' => 'object', 'additionalProperties' => false, 'properties' => $props ?: new \stdClass(),
                    'required' => $required];
        };
        $refresh = ['refresh' => ['type' => 'boolean', 'description' => 'Ask Windows again instead of using the answer from the last ten minutes']];
        $jobId   = ['job_id' => ['type' => 'string', 'description' => 'The job_id a print tool returned']];
        $confirm = ['confirm' => ['type' => 'boolean', 'const' => true,
                                  'description' => 'Must be true. Send it only when EG asked for this print in this conversation']];
        $options = [
            'title'           => ['type' => 'string', 'maxLength' => 120],
            'printer_id'      => ['type' => 'string', 'description' => 'A printer_id from printer_list; omit for EG\'s default printer'],
            'copies'          => ['type' => 'integer', 'minimum' => 1, 'maximum' => 10],
            'page_range'      => ['type' => 'string', 'description' => 'e.g. 1-3,5; omit for every page'],
            'orientation'     => ['type' => 'string', 'enum' => ['portrait', 'landscape']],
            'idempotency_key' => ['type' => 'string', 'maxLength' => 128,
                                  'description' => 'Optional; retrying with the same key returns the same job instead of printing again'],
        ];
        return [
            'printer_list' => ['title' => 'List EG\'s printers',
                'description' => 'The printers Windows has on EG\'s machine, with their status and which is the default. '
                               . 'Reads only; never installs, changes or prints anything. ready=false means the machine is being '
                               . 'asked; call again in a few seconds.',
                'inputSchema' => $object($refresh, []), 'annotations' => $read],
            'printer_get_default' => ['title' => 'Get EG\'s default printer',
                'description' => 'The printer used when a print names none. Reads only.',
                'inputSchema' => $object($refresh, []), 'annotations' => $read],
            'printer_list_documents' => ['title' => 'List printable documents',
                'description' => 'PDF and text files EG placed in the print outbox on his machine, by document_id. '
                               . 'Only these can be printed with printer_print_document; there is no way to print an arbitrary path. Reads only.',
                'inputSchema' => $object($refresh, []), 'annotations' => $read],
            'printer_print_text' => ['title' => 'Print text on EG\'s printer',
                'description' => 'Prints plain text (up to 6000 characters, Unicode) on US Letter, black and white, portrait unless '
                               . 'asked. Runs a real printer: call it only when EG asked for this print, with confirm=true. '
                               . 'test_page=true prints the fixed StarTrekBridge test page instead of text. Returns a job_id; '
                               . 'follow it with printer_get_status. The same text twice within ten minutes is refused as a duplicate.',
                'inputSchema' => $object(['text' => ['type' => 'string', 'maxLength' => 6000],
                                          'test_page' => ['type' => 'boolean']] + $options + $confirm, ['confirm']),
                'annotations' => $prints],
            'printer_print_document' => ['title' => 'Print a document from the outbox',
                'description' => 'Prints one document from printer_list_documents by its document_id. Runs a real printer: '
                               . 'only when EG asked, with confirm=true. Returns a job_id.',
                'inputSchema' => $object(['document_id' => ['type' => 'string']] + $options + $confirm, ['document_id', 'confirm']),
                'annotations' => $prints],
            'printer_get_status' => ['title' => 'Get a print job\'s status',
                'description' => 'accepted, queued, submitting, submitted_to_spooler, completed_per_spooler, canceled, failed or '
                               . 'ambiguous (may have printed; never resent automatically). refresh=true asks Windows again. '
                               . 'Never prints. physical_output_verified is always false: no printer here confirms paper.',
                'inputSchema' => $object($jobId + ['refresh' => ['type' => 'boolean']], ['job_id']), 'annotations' => $read],
            'printer_list_recent' => ['title' => 'List recent print jobs',
                'description' => 'Your most recent bridge print jobs, newest first, with their states. Never prints.',
                'inputSchema' => $object(['limit' => ['type' => 'integer', 'minimum' => 1, 'maximum' => 20]], []),
                'annotations' => $read],
            'printer_cancel' => ['title' => 'Cancel a print job',
                'description' => 'Cancels a job you sent. Before EG\'s machine picks it up, nothing reaches a printer; after that '
                               . 'the job is removed from the Windows queue, and pages already printing may still come out. '
                               . 'Needs confirm=true.',
                'inputSchema' => $object($jobId + $confirm, ['job_id', 'confirm']),
                'annotations' => ['readOnlyHint' => false, 'destructiveHint' => true, 'idempotentHint' => true, 'openWorldHint' => true]],
        ];
    }
}
