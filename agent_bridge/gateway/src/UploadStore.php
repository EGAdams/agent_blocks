<?php
declare(strict_types=1);

namespace AgentBridge;

use PDO;

/**
 * PDF uploads, so a client can hand EG's Notes a real PDF without any shared filesystem path.
 *
 *   notes_upload_begin / _chunk / _finish   the PDF in base64 chunks, each checked in order, then the whole
 *                                           checked against the size and sha256 declared up front
 *   notes_upload_from_url                   the same, from one public https link the gateway fetches itself
 *                                           (a ChatGPT file parameter is such a link)
 *   POST /worker/tasks/{t}/upload           the Notes worker fetching the bytes for a notes_attach_pdf task
 *
 * An upload belongs to the client that began it; only that client's notes_attach_pdf may name it, and the worker
 * gets it only for that very task while holding its lease. Nothing is attached here: attaching is the Notes
 * Review ADW's job on EG's machine, which checks the PDF again before the dashboard stores it.
 */
final class PdfBytes
{
    const MAX_BYTES = 10485760;            // 10 MiB, the dashboard's own limit
    const NAME      = '/^[A-Za-z0-9][A-Za-z0-9 ._()-]{0,150}\.pdf$/i';

    /** Refuses anything that is not one whole PDF within the limit. */
    public static function check(string $data): void
    {
        if ($data === '') {
            throw new BridgeError(422, 'empty', 'no bytes were sent');
        }
        if (strlen($data) > self::MAX_BYTES) {
            throw new BridgeError(413, 'too_large', 'a PDF may be at most 10 MiB');
        }
        if (strncmp($data, '%PDF-', 5) !== 0 || strpos(substr($data, -2048), '%%EOF') === false) {
            throw new BridgeError(422, 'not_a_pdf', 'the bytes are not one whole PDF (it must start %PDF- and end %%EOF)');
        }
    }

    public static function pages(string $data): int
    {
        return (int) preg_match_all('#/Type\s*/Page(?![a-zA-Z])#', $data);
    }

    /** A plain file name ending .pdf, with anything else replaced; never a path. */
    public static function safeName(string $name): string
    {
        $base = basename(str_replace('\\', '/', $name));
        $base = preg_replace('/[^A-Za-z0-9 ._()-]/', '_', $base);
        $base = ltrim((string) $base, '._ ');
        if (!preg_match('/\.pdf$/i', $base)) {
            $base .= '.pdf';
        }
        if (strlen($base) > 150) {
            $base = substr($base, 0, 146) . '.pdf';
        }
        return preg_match(self::NAME, $base) ? $base : 'upload.pdf';
    }
}

final class SqliteUploadStore implements IUploadStore
{
    const COLUMNS = 'upload_id, client_id, name, bytes, sha256, received, state, pages, created_at, completed_at';

    /** @var PDO */          private $pdo;
    /** @var IClock */       private $clock;
    /** @var IIdGenerator */ private $ids;

    public function __construct(PDO $pdo, IClock $clock, IIdGenerator $ids)
    {
        $this->pdo   = $pdo;
        $this->clock = $clock;
        $this->ids   = $ids;
    }

    public function begin(string $clientId, string $name, int $bytes, string $sha256): array
    {
        $id = $this->ids->newId('u_');
        $this->pdo->prepare('INSERT INTO uploads (upload_id, client_id, name, bytes, sha256, state, created_at)
                             VALUES (?, ?, ?, ?, ?, ?, ?)')
            ->execute([$id, $clientId, $name, $bytes, $sha256, 'open', $this->clock->now()]);
        return $this->find($id);
    }

    public function find(string $uploadId): ?array
    {
        $stmt = $this->pdo->prepare('SELECT ' . self::COLUMNS . ' FROM uploads WHERE upload_id = ?');
        $stmt->execute([$uploadId]);
        $row = $stmt->fetch();
        return $row ?: null;
    }

    public function append(string $uploadId, int $offset, string $data): array
    {
        return Database::immediate($this->pdo, function () use ($uploadId, $offset, $data) {
            $row    = $this->find($uploadId);
            $staged = $this->staged($uploadId);
            if ($row === null || $row['state'] !== 'open') {
                throw new BridgeError(409, 'upload_closed', 'that upload is not open');
            }
            $received = strlen($staged);
            if ($offset < $received) {
                if ($offset + strlen($data) <= $received && substr($staged, $offset, strlen($data)) === $data) {
                    return $row;                                   // a repeat of bytes already here: harmless
                }
                throw new BridgeError(409, 'offset_conflict', 'bytes at that offset differ from those received; '
                    . 'continue from offset ' . $received);
            }
            if ($offset > $received) {
                throw new BridgeError(409, 'offset_gap', 'send the chunk at offset ' . $received . ' first');
            }
            if ($received + strlen($data) > (int) $row['bytes']) {
                throw new BridgeError(422, 'too_many_bytes', 'that is more bytes than the upload declared');
            }
            $all  = $staged . $data;
            $stmt = $this->pdo->prepare('UPDATE uploads SET staged = ?, received = ? WHERE upload_id = ?');
            $stmt->bindValue(1, $all, PDO::PARAM_LOB);
            $stmt->bindValue(2, strlen($all), PDO::PARAM_INT);
            $stmt->bindValue(3, $uploadId);
            $stmt->execute();
            return $this->find($uploadId);
        });
    }

    public function staged(string $uploadId): string
    {
        $stmt = $this->pdo->prepare('SELECT staged FROM uploads WHERE upload_id = ?');
        $stmt->execute([$uploadId]);
        $value = $stmt->fetchColumn();
        if (is_resource($value)) {
            $value = stream_get_contents($value);
        }
        return is_string($value) ? $value : '';
    }

    public function markReady(string $uploadId, int $pages): array
    {
        $this->pdo->prepare("UPDATE uploads SET state = 'ready', staged = x'', received = bytes, pages = ?, completed_at = ?
                             WHERE upload_id = ?")->execute([$pages, $this->clock->now(), $uploadId]);
        return $this->find($uploadId);
    }

    public function openCount(string $clientId): int
    {
        $stmt = $this->pdo->prepare("SELECT COUNT(*) FROM uploads WHERE client_id = ? AND state = 'open'");
        $stmt->execute([$clientId]);
        return (int) $stmt->fetchColumn();
    }

    public function sweep(int $seconds): int
    {
        $stmt = $this->pdo->prepare("DELETE FROM uploads WHERE state = 'open' AND created_at < ?");
        $stmt->execute([$this->clock->now() - $seconds]);
        return $stmt->rowCount();
    }
}

/**
 * One public https URL's bytes. Refuses any address that is not public (so a link cannot reach the gateway's own
 * network), pins the connection to the address it checked, follows at most three redirects -- each checked the
 * same way -- and stops reading past the limit.
 */
final class CurlUrlFetcher implements IUrlFetcher
{
    const HOPS = 3;

    /** @var int */ private $timeout;

    public function __construct(int $timeoutSeconds = 30)
    {
        $this->timeout = $timeoutSeconds;
    }

    public function fetch(string $url, int $maxBytes): array
    {
        if (!function_exists('curl_init')) {
            throw new BridgeError(503, 'fetch_unavailable', 'this gateway cannot fetch links; use notes_upload_begin');
        }
        for ($hop = 0; $hop <= self::HOPS; $hop++) {
            list($host, $port, $ip) = self::publicTarget($url);
            $body = '';
            $ch   = curl_init($url);
            curl_setopt_array($ch, [
                CURLOPT_RESOLVE        => [$host . ':' . $port . ':' . $ip],
                CURLOPT_PROTOCOLS      => CURLPROTO_HTTPS,
                CURLOPT_FOLLOWLOCATION => false,
                CURLOPT_TIMEOUT        => $this->timeout,
                CURLOPT_CONNECTTIMEOUT => 10,
                CURLOPT_USERAGENT      => 'agent-bridge-upload/1',
                CURLOPT_HEADER         => false,
                CURLOPT_WRITEFUNCTION  => function ($ch, $chunk) use (&$body, $maxBytes) {
                    if (strlen($body) + strlen($chunk) > $maxBytes) {
                        return 0;                                  // abort: past the limit
                    }
                    $body .= $chunk;
                    return strlen($chunk);
                },
            ]);
            $done     = curl_exec($ch);
            $status   = (int) curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
            $location = (string) curl_getinfo($ch, CURLINFO_REDIRECT_URL);
            $error    = curl_errno($ch);
            curl_close($ch);
            if ($error === CURLE_WRITE_ERROR) {
                throw new BridgeError(413, 'too_large', 'the linked file is larger than 10 MiB');
            }
            if ($done === false) {
                throw new BridgeError(502, 'fetch_failed', 'the link could not be fetched (curl error ' . $error . ')');
            }
            if ($status >= 300 && $status < 400 && $location !== '') {
                $url = $location;
                continue;
            }
            if ($status !== 200) {
                throw new BridgeError(502, 'fetch_failed', 'the link answered HTTP ' . $status);
            }
            return [$body, $host];
        }
        throw new BridgeError(502, 'fetch_failed', 'the link redirected more than ' . self::HOPS . ' times');
    }

    /** @return array{0:string,1:int,2:string} host, port, and the one public IPv4 address it will connect to */
    private static function publicTarget(string $url): array
    {
        $parts = parse_url($url);
        if (!is_array($parts) || strtolower($parts['scheme'] ?? '') !== 'https' || empty($parts['host'])
            || isset($parts['user']) || isset($parts['pass'])) {
            throw new BridgeError(422, 'bad_url', 'only a plain https:// link can be fetched');
        }
        $host = strtolower((string) $parts['host']);
        $port = isset($parts['port']) ? (int) $parts['port'] : 443;
        $ips  = filter_var($host, FILTER_VALIDATE_IP) ? [$host] : (gethostbynamel($host) ?: []);
        if (!$ips) {
            throw new BridgeError(422, 'bad_url', 'that host does not resolve');
        }
        foreach ($ips as $ip) {
            if (!filter_var($ip, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4 | FILTER_FLAG_NO_PRIV_RANGE | FILTER_FLAG_NO_RES_RANGE)) {
                throw new BridgeError(422, 'bad_url', 'that link points at a private or reserved address');
            }
        }
        return [$host, $port, $ips[0]];
    }
}

/**
 * The notes_upload_* MCP tools. They lend NotesTools' permission: a client sees and may use them exactly when it
 * may use notes_attach_pdf, because an upload has no other use.
 */
final class UploadTools implements IToolFamily
{
    const CHUNK_CHARS  = 262144;           // base64 characters per chunk (192 KiB of PDF)
    const MAX_OPEN     = 5;                // unfinished uploads per client
    const OPEN_SECONDS = 86400;            // an unfinished upload is forgotten after a day
    const TOOLS        = ['notes_upload_begin', 'notes_upload_chunk', 'notes_upload_finish', 'notes_upload_from_url'];
    const SHA          = '/^[0-9a-f]{64}$/';
    const ID           = '/^u_[0-9a-f]{24}$/';

    /** @var NotesTools */       private $notes;
    /** @var IUploadStore */     private $uploads;
    /** @var IBlobStore */       private $blobs;
    /** @var IAuditLogger */     private $audit;
    /** @var IUrlFetcher|null */ private $fetcher;

    public function __construct(NotesTools $notes, IUploadStore $uploads, IBlobStore $blobs, IAuditLogger $audit,
                                ?IUrlFetcher $fetcher = null)
    {
        $this->notes   = $notes;
        $this->uploads = $uploads;
        $this->blobs   = $blobs;
        $this->audit   = $audit;
        $this->fetcher = $fetcher;
    }

    public function handles(string $name): bool
    {
        return in_array($name, self::TOOLS, true);
    }

    public function offered(Principal $who): bool
    {
        return $this->notes->permits($who, 'notes_attach_pdf');
    }

    public function instructions(): string
    {
        return 'To attach a PDF to a note, first get it to the gateway: pass a file you have to notes_upload_from_url '
             . '(file, or an https url), or send its bytes with notes_upload_begin (name, bytes, sha256), '
             . 'notes_upload_chunk (base64, in order) and notes_upload_finish. Then call notes_attach_pdf with the note, '
             . 'the upload_id and a title. A sandbox: path or a local file path is never a substitute.';
    }

    public function definitions(Principal $who): array
    {
        if (!$this->offered($who)) {
            return [];
        }
        $write = ['readOnlyHint' => false, 'destructiveHint' => false, 'openWorldHint' => false];
        $id    = ['upload_id' => ['type' => 'string', 'description' => 'The u_... id notes_upload_begin gave']];
        $name  = ['name' => ['type' => 'string', 'description' => 'The PDF\'s file name, e.g. Deed_REVISED.pdf']];
        $sha   = ['sha256' => ['type' => 'string', 'description' => 'The whole PDF\'s SHA-256, 64 lowercase hex']];
        $tools = [
            'notes_upload_begin' => ['title' => 'Begin uploading a PDF',
                'description' => 'Starts a PDF upload for notes_attach_pdf. Declare the whole file\'s size in bytes and its '
                               . 'SHA-256 up front; finish refuses anything else. At most 10 MiB. Then send the bytes with '
                               . 'notes_upload_chunk, in order, from offset 0.',
                'inputSchema' => self::schema($name + $sha + [
                    'bytes' => ['type' => 'integer', 'description' => 'The whole PDF\'s size in bytes'],
                ], ['name', 'bytes', 'sha256']), 'annotations' => $write],
            'notes_upload_chunk' => ['title' => 'Send part of a PDF',
                'description' => 'Sends the next part of an upload as base64 (at most ' . self::CHUNK_CHARS . ' characters, '
                               . 'which is 196608 bytes). offset is the byte position this part starts at: 0, then the '
                               . 'received count each answer gives. Re-sending a part already received is harmless.',
                'inputSchema' => self::schema($id + [
                    'offset' => ['type' => 'integer', 'description' => 'Where these bytes start in the file'],
                    'data_base64' => ['type' => 'string', 'description' => 'The bytes, base64-encoded'],
                ], ['upload_id', 'offset', 'data_base64']), 'annotations' => $write],
            'notes_upload_finish' => ['title' => 'Finish a PDF upload',
                'description' => 'Checks the whole upload -- the declared size and SHA-256, and that it is one whole PDF -- '
                               . 'and stores it. On ready=true, call notes_attach_pdf with this upload_id.',
                'inputSchema' => self::schema($id, ['upload_id']), 'annotations' => $write],
            'notes_upload_from_url' => ['title' => 'Upload a PDF from a file or link',
                'description' => 'Uploads a PDF in one step: pass the file itself (file) or a public https link to it (url). '
                               . 'The gateway fetches it, checks it is one whole PDF of at most 10 MiB (and matches sha256 '
                               . 'when given) and stores it. On ready=true, call notes_attach_pdf with the upload_id. A '
                               . 'sandbox: path is not a link.',
                'inputSchema' => self::schema($name + $sha + [
                    'file' => ['type' => 'object', 'description' => 'The PDF file',
                               'properties' => ['download_url' => ['type' => 'string'], 'file_id' => ['type' => 'string']],
                               'required' => ['download_url'], 'additionalProperties' => true],
                    'url' => ['type' => 'string', 'description' => 'A public https:// link to the PDF'],
                ], []), 'annotations' => $write + ['openWorldHint' => true],
                '_meta' => ['openai/fileParams' => ['file']]],
        ];
        $out = [];
        foreach ($tools as $toolName => $tool) {
            $out[] = ['name' => $toolName] + $tool;
        }
        return $out;
    }

    public function call(Request $r, Principal $who, string $name, array $args): array
    {
        if (!$this->offered($who)) {
            $this->audit->record($who->id, $name, null, 'denied');
            return self::error('not_allowed: this client may not use ' . $name);
        }
        try {
            switch ($name) {
                case 'notes_upload_begin':    $out = $this->begin($who, $args); break;
                case 'notes_upload_chunk':    $out = $this->chunk($who, $args); break;
                case 'notes_upload_finish':   $out = $this->finish($who, $this->own($who, $args)); break;
                default:                      $out = $this->fromUrl($who, $args);
            }
            return self::ok($out);
        } catch (BridgeError $e) {
            $this->audit->record($who->id, $name, null, $e->errorCode);
            return self::error($e->errorCode . ': ' . $e->getMessage());
        }
    }

    private function begin(Principal $who, array $args): array
    {
        $bytes = $args['bytes'] ?? null;
        $sha   = is_string($args['sha256'] ?? null) ? strtolower($args['sha256']) : '';
        if (!is_int($bytes) || $bytes < 1 || $bytes > PdfBytes::MAX_BYTES) {
            throw new BridgeError(422, 'invalid_argument', 'bytes must be the PDF\'s size, 1 to 10485760');
        }
        if (!preg_match(self::SHA, $sha)) {
            throw new BridgeError(422, 'invalid_argument', 'sha256 must be the whole PDF\'s SHA-256 in 64 hex digits');
        }
        $this->uploads->sweep(self::OPEN_SECONDS);
        if ($this->uploads->openCount($who->id) >= self::MAX_OPEN) {
            throw new BridgeError(429, 'too_many_uploads', 'finish or abandon earlier uploads first (they expire in a day)');
        }
        $row = $this->uploads->begin($who->id, PdfBytes::safeName((string) ($args['name'] ?? '')), $bytes, $sha);
        $this->audit->record($who->id, 'notes_upload_begin', null, 'open',
                             ['upload_id' => $row['upload_id'], 'bytes' => $bytes]);
        return ['upload_id' => $row['upload_id'], 'name' => $row['name'], 'bytes' => $bytes, 'received' => 0,
                'chunk_chars' => self::CHUNK_CHARS, 'next' => 'Send the bytes with notes_upload_chunk from offset 0.'];
    }

    private function chunk(Principal $who, array $args): array
    {
        $row    = $this->own($who, $args);
        $offset = $args['offset'] ?? null;
        $data64 = $args['data_base64'] ?? null;
        if (!is_int($offset) || $offset < 0) {
            throw new BridgeError(422, 'invalid_argument', 'offset must be a whole number of bytes, 0 or more');
        }
        if (!is_string($data64) || $data64 === '' || strlen($data64) > self::CHUNK_CHARS) {
            throw new BridgeError(422, 'invalid_argument', 'data_base64 must be 1 to ' . self::CHUNK_CHARS . ' characters');
        }
        $data = base64_decode(preg_replace('/\s+/', '', $data64), true);
        if ($data === false || $data === '') {
            throw new BridgeError(422, 'bad_base64', 'data_base64 is not base64');
        }
        $row = $this->uploads->append($row['upload_id'], $offset, $data);
        $left = (int) $row['bytes'] - (int) $row['received'];
        return ['upload_id' => $row['upload_id'], 'received' => (int) $row['received'], 'remaining' => $left,
                'next' => $left > 0 ? 'Send the chunk at offset ' . $row['received'] . '.' : 'Call notes_upload_finish.'];
    }

    private function finish(Principal $who, array $row): array
    {
        if ($row['state'] === 'ready') {
            return $this->ready($row, false);
        }
        $data = $this->uploads->staged($row['upload_id']);
        if (strlen($data) !== (int) $row['bytes']) {
            throw new BridgeError(409, 'incomplete', 'received ' . strlen($data) . ' of ' . $row['bytes'] . ' bytes; '
                . 'send the chunk at offset ' . strlen($data));
        }
        if (!hash_equals((string) $row['sha256'], hash('sha256', $data))) {
            throw new BridgeError(422, 'hash_mismatch', 'the bytes received do not hash to the declared sha256; '
                . 'begin again');
        }
        PdfBytes::check($data);
        $this->blobs->put((string) $row['sha256'], $data);
        $row = $this->uploads->markReady($row['upload_id'], PdfBytes::pages($data));
        $this->audit->record($who->id, 'notes_upload_finish', null, 'ready',
                             ['upload_id' => $row['upload_id'], 'bytes' => (int) $row['bytes']]);
        return $this->ready($row, true);
    }

    private function fromUrl(Principal $who, array $args): array
    {
        $file = is_array($args['file'] ?? null) ? $args['file'] : [];
        $url  = is_string($file['download_url'] ?? null) ? $file['download_url'] : (is_string($args['url'] ?? null) ? $args['url'] : '');
        if ($url === '' || strlen($url) > 4096) {
            throw new BridgeError(422, 'invalid_argument', 'pass file (the PDF) or url (a public https link to it)');
        }
        if (stripos($url, 'sandbox:') === 0 || $url[0] === '/') {
            throw new BridgeError(422, 'bad_url', 'a sandbox: or local path is not reachable from here; pass the file '
                . 'itself, or send the bytes with notes_upload_begin');
        }
        if ($this->fetcher === null) {
            throw new BridgeError(503, 'fetch_unavailable', 'this gateway cannot fetch links; use notes_upload_begin');
        }
        $declared = is_string($args['sha256'] ?? null) ? strtolower($args['sha256']) : '';
        if ($declared !== '' && !preg_match(self::SHA, $declared)) {
            throw new BridgeError(422, 'invalid_argument', 'sha256 must be 64 hex digits');
        }
        list($data, $host) = $this->fetcher->fetch($url, PdfBytes::MAX_BYTES);
        PdfBytes::check($data);
        $sha = hash('sha256', $data);
        if ($declared !== '' && !hash_equals($declared, $sha)) {
            throw new BridgeError(422, 'hash_mismatch', 'the fetched file does not hash to the sha256 given');
        }
        $this->uploads->sweep(self::OPEN_SECONDS);
        $fallback = is_string($file['file_id'] ?? null) ? $file['file_id'] : basename((string) parse_url($url, PHP_URL_PATH));
        $row = $this->uploads->begin($who->id, PdfBytes::safeName((string) ($args['name'] ?? $fallback)), strlen($data), $sha);
        $this->blobs->put($sha, $data);
        $row = $this->uploads->markReady($row['upload_id'], PdfBytes::pages($data));
        $this->audit->record($who->id, 'notes_upload_from_url', null, 'ready',
                             ['upload_id' => $row['upload_id'], 'bytes' => strlen($data), 'host' => $host]);
        return $this->ready($row, true);
    }

    /** The caller's own upload, or not found -- another client's upload is indistinguishable from none. */
    private function own(Principal $who, array $args): array
    {
        $id  = is_string($args['upload_id'] ?? null) ? $args['upload_id'] : '';
        $row = preg_match(self::ID, $id) ? $this->uploads->find($id) : null;
        if ($row === null || !hash_equals((string) $row['client_id'], $who->id)) {
            throw new BridgeError(404, 'not_found', 'no such upload (begin one with notes_upload_begin)');
        }
        return $row;
    }

    private function ready(array $row, bool $stored): array
    {
        return ['upload_id' => $row['upload_id'], 'ready' => true, 'stored' => $stored, 'name' => $row['name'],
                'bytes' => (int) $row['bytes'], 'sha256' => $row['sha256'], 'pages' => (int) $row['pages'],
                'next' => 'Call notes_attach_pdf with the note, this upload_id and a title.'];
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

    private static function schema(array $props, array $required): array
    {
        return ['type' => 'object', 'additionalProperties' => false, 'properties' => $props, 'required' => $required];
    }
}

/**
 * The Notes worker's one way to an upload's bytes: for a running notes_attach_pdf task it holds the lease of,
 * whose prompt names the upload, and only when the upload belongs to the client that submitted that task.
 */
final class WorkerUploads
{
    /** @var ITaskRepository */   private $tasks;
    /** @var ITaskLeaseManager */ private $leases;
    /** @var IUploadStore */      private $uploads;
    /** @var IBlobStore */        private $blobs;
    /** @var IAuditLogger */      private $audit;
    /** @var int */               private $leaseSeconds;

    public function __construct(ITaskRepository $tasks, ITaskLeaseManager $leases, IUploadStore $uploads, IBlobStore $blobs,
                                IAuditLogger $audit, int $leaseSeconds)
    {
        $this->tasks        = $tasks;
        $this->leases       = $leases;
        $this->uploads      = $uploads;
        $this->blobs        = $blobs;
        $this->audit        = $audit;
        $this->leaseSeconds = $leaseSeconds;
    }

    public function fetch(Principal $who, string $taskId, string $lease): array
    {
        $task = $this->tasks->find($taskId);
        $req  = $task === null ? null : json_decode((string) $task['prompt'], true);
        $id   = is_array($req) && is_string($req['upload_id'] ?? null) ? $req['upload_id'] : '';
        if ($task === null || $task['agent'] !== NotesTools::AGENT || $task['state'] !== TaskState::RUNNING
            || ($req['notes'] ?? null) !== 'review_attach_pdf' || $id === '') {
            throw new BridgeError(404, 'not_found', 'no such running notes_attach_pdf task');
        }
        if (!preg_match('/^[A-Za-z0-9_-]{20,200}$/', $lease)
            || $this->leases->heartbeat($taskId, $lease, $this->leaseSeconds) === null) {
            throw new BridgeError(409, 'lease_invalid', 'the lease is expired or not yours');
        }
        $row = $this->uploads->find($id);
        if ($row === null || !hash_equals((string) $row['client_id'], (string) $task['client_id'])) {
            $this->audit->record($who->id, 'upload_fetch', $taskId, 'refused');
            throw new BridgeError(404, 'not_found', 'that upload is not the submitting client\'s');
        }
        if ($row['state'] !== 'ready') {
            throw new BridgeError(409, 'upload_not_ready', 'that upload was never finished');
        }
        $data = $this->blobs->read((string) $row['sha256']);
        $this->audit->record($who->id, 'upload_fetch', $taskId, 'sent', ['upload_id' => $id, 'bytes' => strlen($data)]);
        return ['upload_id' => $id, 'name' => $row['name'], 'bytes' => strlen($data), 'sha256' => $row['sha256'],
                'pages' => (int) $row['pages'], 'pdf_base64' => base64_encode($data)];
    }
}
