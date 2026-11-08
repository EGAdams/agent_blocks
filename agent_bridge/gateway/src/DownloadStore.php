<?php
declare(strict_types=1);

namespace AgentBridge;

use PDO;

/**
 * Browser downloads, as rows. The bytes are FileBlobStore's (the same store the
 * scanner's pages use), named by sha256; this holds only which task saved which
 * file, what it really is, its text, and which blobs are its page images.
 */
final class SqliteDownloadStore implements IDownloadStore
{
    const LISTED = 'file_id, task_id, idx, client_id, name, original_name, saved_path, mime, bytes, sha256, original_stored,
                    kind, page_count, text_truncated, note, created_at, length(text) AS text_chars';

    /** @var PDO */    private $pdo;
    /** @var IClock */ private $clock;

    public function __construct(PDO $pdo, IClock $clock)
    {
        $this->pdo   = $pdo;
        $this->clock = $clock;
    }

    public function saveTask(string $taskId, array $files): void
    {
        $now = $this->clock->now();
        Database::immediate($this->pdo, function () use ($taskId, $files, $now) {
            $this->pdo->prepare('DELETE FROM download_pages WHERE file_id IN (SELECT file_id FROM downloads WHERE task_id = ?)')
                ->execute([$taskId]);
            $this->pdo->prepare('DELETE FROM downloads WHERE task_id = ?')->execute([$taskId]);
            $row  = $this->pdo->prepare(
                'INSERT INTO downloads (file_id, task_id, idx, client_id, name, original_name, saved_path, mime, bytes, sha256,
                                        original_stored, kind, page_count, text, text_truncated, note, created_at)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)');
            $page = $this->pdo->prepare('INSERT INTO download_pages (file_id, page, rendition, sha256, mime, width, height, bytes)
                                         VALUES (?, ?, ?, ?, ?, ?, ?, ?)');
            foreach ($files as $f) {
                $row->execute([$f['file_id'], $taskId, $f['idx'], $f['client_id'], $f['name'], $f['original_name'],
                               $f['saved_path'], $f['mime'], $f['bytes'], $f['sha256'], $f['original_stored'] ? 1 : 0,
                               $f['kind'], $f['page_count'], $f['text'], $f['text_truncated'] ? 1 : 0, $f['note'], $now]);
                foreach ($f['pages'] as $p) {
                    $page->execute([$f['file_id'], $p['page'], $p['rendition'], $p['sha256'], $p['mime'],
                                    $p['width'], $p['height'], $p['bytes']]);
                }
            }
        });
    }

    public function find(string $fileId): ?array
    {
        $stmt = $this->pdo->prepare('SELECT * FROM downloads WHERE file_id = ?');
        $stmt->execute([$fileId]);
        $row = $stmt->fetch();
        return $row ?: null;
    }

    public function byTask(string $taskId): array
    {
        $stmt = $this->pdo->prepare('SELECT ' . self::LISTED . ' FROM downloads WHERE task_id = ? ORDER BY idx');
        $stmt->execute([$taskId]);
        return $stmt->fetchAll();
    }

    public function recent(string $clientId, int $limit): array
    {
        $stmt = $this->pdo->prepare('SELECT ' . self::LISTED . ' FROM downloads WHERE client_id = ?
                                     ORDER BY created_at DESC, task_id DESC, idx LIMIT ?');
        $stmt->execute([$clientId, max(1, min(100, $limit))]);
        return $stmt->fetchAll();
    }

    public function pages(string $fileId): array
    {
        $stmt = $this->pdo->prepare('SELECT * FROM download_pages WHERE file_id = ? ORDER BY page, rendition');
        $stmt->execute([$fileId]);
        return $stmt->fetchAll();
    }

    public function page(string $fileId, int $page, string $rendition): ?array
    {
        $stmt = $this->pdo->prepare('SELECT * FROM download_pages WHERE file_id = ? AND page = ? AND rendition = ?');
        $stmt->execute([$fileId, $page, $rendition]);
        $row = $stmt->fetch();
        return $row ?: null;
    }
}

/**
 * The worker's side of downloads: the saved file and its page images go up one
 * blob at a time (by sha256), then one manifest binds them to the browser
 * download task -- only while the worker still holds that task's lease, and
 * only for a task that really is a browser download. Answers with a file_id per
 * file, which is what browser_view_file takes.
 */
final class WorkerDownloads
{
    const MAX_FILES     = 20;
    const MAX_PAGES     = 40;            // renditions per file: 20 pages, two sizes
    const MAX_TEXT      = 204800;        // bytes of text per task, all files together
    const KINDS         = ['image', 'pdf', 'text', 'document', 'spreadsheet', 'archive', 'binary'];
    const PAGE_MIMES    = ['image/jpeg', 'image/png'];

    /** @var ITaskRepository */   private $tasks;
    /** @var ITaskLeaseManager */ private $leases;
    /** @var IDownloadStore */    private $downloads;
    /** @var IBlobStore */        private $blobs;
    /** @var IAuditLogger */      private $audit;
    /** @var int */               private $leaseSeconds;

    public function __construct(ITaskRepository $tasks, ITaskLeaseManager $leases, IDownloadStore $downloads, IBlobStore $blobs,
                                IAuditLogger $audit, int $leaseSeconds)
    {
        $this->tasks        = $tasks;
        $this->leases       = $leases;
        $this->downloads    = $downloads;
        $this->blobs        = $blobs;
        $this->audit        = $audit;
        $this->leaseSeconds = $leaseSeconds;
    }

    public static function fileId(string $taskId, int $idx): string
    {
        return 'f_' . substr(hash('sha256', $taskId . '/' . $idx), 0, 24);
    }

    public function blob(Principal $who, string $taskId, string $sha256, string $bytes): array
    {
        $this->downloadTask($taskId);
        if ($bytes === '') {
            throw new BridgeError(422, 'empty_blob', 'no bytes were sent');
        }
        $this->blobs->put($sha256, $bytes);
        $this->audit->record($who->id, 'download_blob', $taskId, 'stored', ['bytes' => strlen($bytes)]);
        return ['sha256' => $sha256, 'stored' => true, 'bytes' => strlen($bytes)];
    }

    public function manifest(Principal $who, string $taskId, array $body): array
    {
        $lease = isset($body['lease_token']) && is_string($body['lease_token']) ? $body['lease_token'] : '';
        if (($body['task_id'] ?? null) !== $taskId || !preg_match('/^[A-Za-z0-9_-]{20,200}$/', $lease)) {
            throw new BridgeError(422, 'invalid_field', 'task_id (this task) and lease_token are required');
        }
        $task  = $this->downloadTask($taskId);
        $list  = isset($body['files']) && is_array($body['files']) ? array_values($body['files']) : [];
        if (!$list || count($list) > self::MAX_FILES) {
            throw new BridgeError(422, 'invalid_files', 'files must list 1 to ' . self::MAX_FILES . ' saved files');
        }
        $files = [];
        $text  = 0;
        foreach ($list as $f) {
            $file  = $this->file(is_array($f) ? $f : [], $taskId, (string) $task['client_id']);
            $text += strlen($file['text']);
            if ($text > self::MAX_TEXT) {
                throw new BridgeError(422, 'text_too_large', 'at most ' . self::MAX_TEXT . ' bytes of text per download');
            }
            if (isset($files[$file['idx']])) {
                throw new BridgeError(422, 'invalid_file', 'two files share idx ' . $file['idx']);
            }
            $files[$file['idx']] = $file;
        }
        if ($this->leases->heartbeat($taskId, $lease, $this->leaseSeconds) === null) {
            throw new BridgeError(409, 'lease_invalid', 'the lease is expired or not yours');
        }
        $this->downloads->saveTask($taskId, array_values($files));
        $this->audit->record($who->id, 'download_manifest', $taskId, 'saved', ['files' => count($files)]);
        return ['task_id' => $taskId, 'saved' => true, 'files' => array_values(array_map(function ($f) {
            return ['idx' => $f['idx'], 'file_id' => $f['file_id'], 'name' => $f['name']];
        }, $files))];
    }

    private function downloadTask(string $taskId): array
    {
        $task = $this->tasks->find($taskId);
        $req  = $task === null ? null : json_decode((string) $task['prompt'], true);
        if ($task === null || $task['agent'] !== 'browser' || !is_array($req) || ($req['browser'] ?? null) !== 'download') {
            throw new BridgeError(404, 'not_found', 'no such browser download task');
        }
        return $task;
    }

    private function file(array $f, string $taskId, string $clientId): array
    {
        $idx = $f['idx'] ?? null;
        $sha = $f['sha256'] ?? null;
        $ok  = is_int($idx) && $idx >= 0 && $idx < self::MAX_FILES
            && is_string($sha) && preg_match(FileBlobStore::SHA, $sha)
            && isset($f['bytes']) && is_int($f['bytes']) && $f['bytes'] >= 1
            && is_string($f['mime'] ?? null) && preg_match('#^[a-z0-9.+-]{1,40}/[a-z0-9.+-]{1,100}$#', $f['mime'])
            && in_array($f['kind'] ?? null, self::KINDS, true);
        if (!$ok) {
            throw new BridgeError(422, 'invalid_file', 'each file needs idx, sha256, bytes, a mime and a kind');
        }
        $stored = !empty($f['original_stored']);
        if ($stored && !$this->blobs->has($sha)) {
            throw new BridgeError(422, 'missing_blob', 'upload the file before naming it stored');
        }
        $text = is_string($f['text'] ?? null) ? $f['text'] : '';
        if ($text !== '' && !preg_match('//u', $text)) {
            throw new BridgeError(422, 'invalid_text', 'text must be UTF-8');
        }
        return ['file_id' => self::fileId($taskId, $idx), 'idx' => $idx, 'client_id' => $clientId,
                'name' => self::short($f['name'] ?? '', 255) ?: 'download',
                'original_name' => self::short($f['original_name'] ?? '', 255) ?: self::short($f['name'] ?? '', 255),
                'saved_path' => self::short($f['saved_path'] ?? '', 500),
                'mime' => $f['mime'], 'bytes' => $f['bytes'], 'sha256' => $sha, 'original_stored' => $stored,
                'kind' => $f['kind'], 'page_count' => isset($f['page_count']) && is_int($f['page_count']) ? max(0, $f['page_count']) : 0,
                'text' => $text, 'text_truncated' => !empty($f['text_truncated']),
                'note' => self::short($f['note'] ?? '', 500),
                'pages' => $this->pages(isset($f['pages']) && is_array($f['pages']) ? $f['pages'] : [])];
    }

    private function pages(array $list): array
    {
        if (count($list) > self::MAX_PAGES) {
            throw new BridgeError(422, 'too_many_pages', 'at most ' . self::MAX_PAGES . ' page renditions per file');
        }
        $out = [];
        foreach ($list as $p) {
            $ok = is_array($p) && isset($p['page'], $p['width'], $p['height'], $p['bytes'])
                && is_int($p['page']) && $p['page'] >= 1 && $p['page'] <= 500
                && in_array($p['rendition'] ?? null, ['preview', 'full'], true)
                && in_array($p['mime'] ?? null, self::PAGE_MIMES, true)
                && is_string($p['sha256'] ?? null) && preg_match(FileBlobStore::SHA, $p['sha256'])
                && is_int($p['width']) && is_int($p['height']) && is_int($p['bytes']);
            if (!$ok) {
                throw new BridgeError(422, 'invalid_page', 'each page needs page, rendition, an image mime, sha256, width, height, bytes');
            }
            if (!$this->blobs->has($p['sha256'])) {
                throw new BridgeError(422, 'missing_blob', 'upload page ' . $p['page'] . ' before the manifest');
            }
            $out[$p['page'] . '/' . $p['rendition']] = ['page' => $p['page'], 'rendition' => $p['rendition'],
                'sha256' => $p['sha256'], 'mime' => $p['mime'], 'width' => $p['width'], 'height' => $p['height'], 'bytes' => $p['bytes']];
        }
        return array_values($out);
    }

    private static function short($value, int $max): string
    {
        $text = is_string($value) ? preg_replace('/[\x00-\x1f\x7f]/u', '', $value) : '';
        return mb_substr((string) $text, 0, $max);
    }
}
