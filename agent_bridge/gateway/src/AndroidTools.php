<?php
declare(strict_types=1);

namespace AgentBridge;

require_once __DIR__ . '/AgentTaskTools.php';

/**
 * The android_* MCP tools: EG's own photographs, read off the Android phone plugged into the Windows computer by
 * USB, through the Bridge `android-photos` agent, which runs the Android Photos ADW (adws/adw_android_photos.py)
 * on that machine. Read-only from end to end: there is no tool here, no worker operation and no reader verb that
 * changes, moves or deletes anything on the phone.
 *
 * The gateway only shapes the request and hands back the pictures. The worker publishes each requested picture
 * through the same download store browser_download uses (a thumbnail for a listing, the photograph for
 * android_get_photo), and this class adds them to the tool answer as MCP image content -- the picture itself, not
 * a Windows path. Only the client that asked sees them: a file belongs to the client whose task produced it.
 *
 * A phone that is absent, locked, not in File transfer mode or not on the machine's allowlist is answered with
 * what EG has to do. It is never worked around.
 */
final class AndroidTools extends AgentTaskTools
{
    const AGENT     = 'android-photos';
    const WORKSPACE = 'android-photos';

    const DEFAULT_COUNT = 15;
    const MAX_COUNT     = 20;
    const MAX_NAME      = 180;
    /** The picture bytes one answer may carry; past it the rest are named and fetched one at a time. */
    const MAX_IMAGE_BYTES = 6291456;

    /** The worker operations whose task may publish files (WorkerDownloads asks). */
    const PUBLISHES = ['list_recent_photos', 'get_photo', 'get_thumbnail'];

    /** tool => [worker operation, task operation, allowed fields, required fields]. */
    const TOOLS = [
        'android_status'             => ['status', 'inspect', [], []],
        'android_list_recent_photos' => ['list_recent_photos', 'inspect', ['count'], []],
        'android_get_photo'          => ['get_photo', 'inspect', ['number', 'name'], []],
        'android_get_thumbnail'      => ['get_thumbnail', 'inspect', ['number', 'name'], []],
        'android_get_result'         => [null, 'inspect', [], []],
    ];

    const DETAIL_KEYS = ['android', 'state', 'device', 'devices_seen', 'folders', 'photo_count', 'total_on_phone',
                         'photos', 'notes', 'next_step', 'inspectable', 'inspect_problem', 'adw_id'];

    /** What EG has to do, by the state the machine reported. Never a way round. */
    const NEXT = [
        'no_device'        => 'No phone is attached. Ask EG to plug the phone into the Windows computer with a USB data '
                            . 'cable, unlock it and choose "File transfer" on the phone; then ask again.',
        'locked'           => 'The phone is attached but locked, or not in File transfer mode. Ask EG to unlock it and '
                            . 'choose "File transfer" on its USB notification; then ask again. Do not try anything else.',
        'not_authorized'   => 'A phone is attached but EG has not authorized it for photo access. Only EG can, on that '
                            . 'computer (adw_android_authorize.py); it cannot be done from here.',
        'no_camera_folder' => 'The phone has none of the permitted camera folders. Tell EG; the permitted folders are '
                            . 'set on that computer.',
        'not_found'        => 'There is no such photo in the last listing. Call android_list_recent_photos, then ask for '
                            . 'one of them by its number.',
        'unreadable'       => 'That picture could not be turned into something viewable. Say so; do not describe it.',
        'no_windows'       => 'The machine cannot reach Windows right now, so the phone cannot be read. Tell EG.',
    ];

    /** @var IDownloadStore|null */ private $downloads;
    /** @var IBlobStore|null */     private $blobs;

    public function __construct(ClientTasks $client, ClientScopes $scopes, IAuditLogger $audit, array $grants,
                                int $waitSeconds = 45, ?callable $sleep = null, ?IDownloadStore $downloads = null,
                                ?IBlobStore $blobs = null)
    {
        parent::__construct($client, $scopes, $audit, $grants, $waitSeconds, $sleep);
        $this->downloads = $downloads;
        $this->blobs     = $blobs;
    }

    protected function agent(): string { return self::AGENT; }
    protected function workspace(): string { return self::WORKSPACE; }
    protected function tools(): array { return self::TOOLS; }
    protected function detailKeys(): array { return self::DETAIL_KEYS; }

    public function instructions(): string
    {
        return 'The android_* tools show EG the photographs on their own Android phone, plugged into their Windows '
             . 'computer by USB. They only read. "Show me my last fifteen pictures" -> android_list_recent_photos '
             . '(count 15 is the default; "the five newest" -> count 5); the answer carries the thumbnails as images, '
             . 'newest first, numbered from 1 -- show them. "Open picture number three" -> android_get_photo with '
             . 'number 3, which returns the photograph itself. "When did I take that picture?" -> the taken_at of '
             . 'that photo in the answer you already have (taken_source says whether it came from the camera\'s own '
             . 'record, the file name or the file date). "Refresh my pictures" -> android_list_recent_photos again; '
             . 'numbers always refer to the latest listing. If state is not "ready", tell EG exactly what next_step '
             . 'says (plug in, unlock, choose File transfer) and stop: never attempt another route to the phone. '
             . 'Never describe a picture you were not given.';
    }

    protected function requestFor(string $op, array $args, array $allowed, array $required): array
    {
        $out = ['android' => $op];
        if (in_array('count', $allowed, true)) {
            $out['count'] = array_key_exists('count', $args) && $args['count'] !== null
                ? self::whole($args['count'], 'count') : self::DEFAULT_COUNT;
        }
        if (in_array('number', $allowed, true)) {
            $number = isset($args['number']) ? self::whole($args['number'], 'number') : null;
            $name   = isset($args['name']) && $args['name'] !== '' ? $args['name'] : null;
            if (($number === null) === ($name === null)) {
                throw new BridgeError(422, 'invalid_argument', 'give number (its place in the last listing) or name, '
                    . 'one of them');
            }
            if ($name !== null) {
                if (!is_string($name) || mb_strlen($name) > self::MAX_NAME
                    || !preg_match('/^[^\/\\\\:*?"<>|\x00-\x1f]+$/', $name) || $name[0] === '.' || $name[0] === '-') {
                    throw new BridgeError(422, 'invalid_argument', 'name is a photo\'s file name exactly as the listing '
                        . 'gave it, with no folder');
                }
                $out['name'] = $name;
            } else {
                $out['number'] = $number;
            }
        }
        return $out;
    }

    /** 1..MAX_COUNT as an integer; a model that sends 5.0 means 5. */
    private static function whole($value, string $what): int
    {
        if (is_float($value) && floor($value) === $value) {
            $value = (int) $value;
        }
        if (!is_int($value) || $value < 1 || $value > self::MAX_COUNT) {
            throw new BridgeError(422, 'invalid_argument', $what . ' must be a whole number from 1 to ' . self::MAX_COUNT);
        }
        return $value;
    }

    /** The parent's answer, then the pictures themselves. */
    public function call(Request $r, Principal $who, string $name, array $args): array
    {
        $answer = parent::call($r, $who, $name, $args);
        $view   = isset($answer['structuredContent']) ? $answer['structuredContent'] : null;
        if (!empty($answer['isError']) || !is_array($view) || empty($view['ready']) || $this->downloads === null
            || $this->blobs === null || !isset($view['photos']) || !is_array($view['photos'])) {
            return $answer;
        }
        $want = isset($view['android']) && $view['android'] === 'get_photo' ? ['full', 'preview'] : ['preview', 'full'];
        $sent = 0;
        $left = [];
        $spent = 0;
        foreach ($view['photos'] as $photo) {
            $photo  = (array) $photo;
            $fileId = isset($photo['file_id']) && is_string($photo['file_id']) ? $photo['file_id'] : '';
            $number = isset($photo['number']) ? (int) $photo['number'] : 0;
            $image  = $this->image($who, $fileId, $want, self::MAX_IMAGE_BYTES - $spent);
            if ($image === null) {
                if ($fileId !== '') {
                    $left[] = $number;
                }
                continue;
            }
            $spent += $image[1];
            $taken  = isset($photo['taken_at']) && $photo['taken_at'] !== '' ? ', taken ' . $photo['taken_at'] : '';
            $answer['content'][] = ['type' => 'text', 'text' => 'Photo ' . $number . ': '
                . (isset($photo['name']) ? (string) $photo['name'] : '') . $taken];
            $answer['content'][] = $image[0];
            $sent++;
        }
        if ($left) {
            $answer['content'][] = ['type' => 'text', 'text' => 'Not included here (the answer was full or the picture '
                . 'is not available): photo ' . implode(', ', $left) . '. Ask for each with android_get_thumbnail.'];
        }
        if ($sent) {
            $this->audit->record($who->id, $name, (string) $view['task_id'], 'images', ['images' => $sent, 'bytes' => $spent]);
        }
        return $answer;
    }

    /** [MCP image block, bytes] for the caller's own file, in the first of the wanted renditions that fits. */
    private function image(Principal $who, string $fileId, array $want, int $room): ?array
    {
        if (!preg_match('/^f_[0-9a-f]{24}$/', $fileId)) {
            return null;
        }
        $row = $this->downloads->find($fileId);
        if ($row === null || !hash_equals((string) $row['client_id'], $who->id)) {
            return null;
        }
        foreach ($want as $rendition) {
            $page = $this->downloads->page($fileId, 1, $rendition);
            if ($page === null) {
                continue;
            }
            $bytes = $this->blobs->read((string) $page['sha256']);
            if ($bytes === '' || strlen($bytes) > $room) {
                continue;
            }
            return [['type' => 'image', 'data' => base64_encode($bytes), 'mimeType' => (string) $page['mime']], strlen($bytes)];
        }
        return null;
    }

    protected function pending(): string
    {
        return 'EG\'s machine is still copying the pictures off the phone (the first time takes longest). Call '
             . 'android_get_result with this task_id; do not ask again, it would only start a second copy.';
    }

    protected function notDone(array $details): string
    {
        $state = isset($details['state']) ? (string) $details['state'] : '';
        if (isset(self::NEXT[$state])) {
            return self::NEXT[$state];
        }
        if (!empty($details['inspect_problem'])) {
            return 'The pictures were read but could not be delivered. Ask once more; if it repeats, tell EG.';
        }
        return 'Not done: do not describe pictures you were not given. Read the line and next_step, and tell EG.';
    }

    protected function definitionTable(): array
    {
        $read  = ['readOnlyHint' => true, 'destructiveHint' => false, 'openWorldHint' => false];
        $which = [
            'number' => ['type' => 'integer', 'minimum' => 1, 'maximum' => self::MAX_COUNT,
                         'description' => 'The photo\'s number in the latest android_list_recent_photos answer (1 = newest)'],
            'name'   => ['type' => 'string', 'description' => 'Or its file name exactly as the listing gave it'],
        ];
        $after = ' Waits up to about 45 seconds for EG\'s machine; if ready=false, call android_get_result with the '
               . 'task_id rather than asking again.';
        $only  = ' Read-only: nothing on the phone is ever changed.';
        return [
            'android_status' => ['title' => 'Is the phone connected and readable',
                'description' => 'Whether EG\'s Android phone is attached by USB, unlocked, authorized and showing its '
                               . 'camera folders, and how many photos they hold. If not ready, next_step says what EG '
                               . 'has to do.' . $only . $after,
                'inputSchema' => self::schema([], []), 'annotations' => $read],
            'android_list_recent_photos' => ['title' => 'The newest photos on the phone, as thumbnails',
                'description' => 'The newest photos EG took, newest first, numbered from 1, with when each was taken, '
                               . 'and each one\'s thumbnail as an image in the answer. Use it for "show me my last '
                               . 'fifteen pictures" and for "refresh my pictures".' . $only . $after,
                'inputSchema' => self::schema(['count' => ['type' => 'integer', 'minimum' => 1, 'maximum' => self::MAX_COUNT,
                    'description' => 'How many (default 15)']], []), 'annotations' => $read],
            'android_get_photo' => ['title' => 'Open one photo at full size',
                'description' => 'One photograph at viewing size, as an image in the answer, with when it was taken. '
                               . 'Use it for "open picture number three".' . $only . $after,
                'inputSchema' => self::schema($which, []), 'annotations' => $read],
            'android_get_thumbnail' => ['title' => 'One photo\'s thumbnail',
                'description' => 'One photo\'s thumbnail as an image in the answer; for a photo a listing could not '
                               . 'include.' . $only . $after,
                'inputSchema' => self::schema($which, []), 'annotations' => $read],
            'android_get_result' => ['title' => 'The answer to an android_* request that was still running',
                'description' => 'The result of an earlier android_* call that returned ready=false, pictures included.',
                'inputSchema' => self::schema(['task_id' => ['type' => 'string', 'description' => 'The task_id that call gave']],
                                              ['task_id']), 'annotations' => $read],
        ];
    }
}
