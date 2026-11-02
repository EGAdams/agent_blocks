<?php
declare(strict_types=1);

namespace AgentBridge;

use PDO;

/**
 * Scan bytes on disk, named only by their sha256. Outside the web root, 0700
 * directories and 0600 files, written to a temp name then renamed so a reader
 * never sees half a file, and hashed again on every read.
 */
final class FileBlobStore implements IBlobStore
{
    const SHA = '/^[0-9a-f]{64}$/';

    /** @var string */ private $dir;

    public function __construct(string $dir)
    {
        $this->dir = rtrim($dir, '/');
    }

    public function put(string $sha256, string $bytes): void
    {
        $path = $this->path($sha256);
        if (!hash_equals($sha256, hash('sha256', $bytes))) {
            throw new BridgeError(422, 'hash_mismatch', 'the bytes do not hash to the sha256 in the URL');
        }
        if ($this->has($sha256)) {
            return;
        }
        $parent = dirname($path);
        if (!is_dir($parent) && !@mkdir($parent, 0700, true) && !is_dir($parent)) {
            throw new BridgeError(503, 'storage_unavailable', 'scan storage is not writable');
        }
        $tmp = $parent . '/.' . $sha256 . '.' . bin2hex(random_bytes(6)) . '.tmp';
        if (@file_put_contents($tmp, $bytes) !== strlen($bytes)) {
            @unlink($tmp);
            throw new BridgeError(503, 'storage_unavailable', 'scan storage is not writable');
        }
        @chmod($tmp, 0600);
        if (!@rename($tmp, $path)) {
            @unlink($tmp);
            throw new BridgeError(503, 'storage_unavailable', 'scan storage is not writable');
        }
    }

    public function has(string $sha256): bool
    {
        $path = $this->path($sha256);
        return is_file($path) && !is_link($path);
    }

    public function read(string $sha256): string
    {
        $path = $this->path($sha256);
        if (!is_file($path) || is_link($path)) {
            throw new BridgeError(404, 'not_found', 'that page is not stored');
        }
        $bytes = (string) @file_get_contents($path);
        if (!hash_equals($sha256, hash('sha256', $bytes))) {
            throw new BridgeError(500, 'artifact_corrupt', 'a stored page failed its integrity check');
        }
        return $bytes;
    }

    private function path(string $sha256): string
    {
        if (!preg_match(self::SHA, $sha256)) {
            throw new BridgeError(422, 'invalid_sha256', 'sha256 must be 64 lower-case hex characters');
        }
        return $this->dir . '/' . substr($sha256, 0, 2) . '/' . $sha256;
    }
}

final class SqliteScanStore implements IScanStore
{
    const ACTIVE = ["'queued'", "'running'"];

    /** @var PDO */    private $pdo;
    /** @var IClock */ private $clock;

    public function __construct(PDO $pdo, IClock $clock)
    {
        $this->pdo   = $pdo;
        $this->clock = $clock;
    }

    public function save(array $scan, array $pages, array $texts): void
    {
        $now = $this->clock->now();
        Database::immediate($this->pdo, function () use ($scan, $pages, $texts, $now) {
            $this->pdo->prepare(
                'INSERT INTO scans (scan_id, task_id, client_id, scanner, status, record, original_sha256, original_mime,
                                    original_bytes, original_stored, page_count, created_at, updated_at)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                 ON CONFLICT (scan_id) DO UPDATE SET status = excluded.status, record = excluded.record,
                     original_sha256 = excluded.original_sha256, original_mime = excluded.original_mime,
                     original_bytes = excluded.original_bytes, original_stored = excluded.original_stored,
                     page_count = excluded.page_count, updated_at = excluded.updated_at')
                ->execute([$scan['scan_id'], $scan['task_id'], $scan['client_id'], $scan['scanner'], $scan['status'],
                           $scan['record'], $scan['original_sha256'], $scan['original_mime'], $scan['original_bytes'],
                           $scan['original_stored'] ? 1 : 0, (int) $scan['page_count'], $now, $now]);
            $this->pdo->prepare('DELETE FROM scan_pages WHERE scan_id = ?')->execute([$scan['scan_id']]);
            $this->pdo->prepare('DELETE FROM scan_texts WHERE scan_id = ?')->execute([$scan['scan_id']]);
            $page = $this->pdo->prepare('INSERT INTO scan_pages (scan_id, page, rendition, sha256, mime, width, height, bytes)
                                         VALUES (?, ?, ?, ?, ?, ?, ?, ?)');
            foreach ($pages as $p) {
                $page->execute([$scan['scan_id'], $p['page'], $p['rendition'], $p['sha256'], $p['mime'],
                                $p['width'], $p['height'], $p['bytes']]);
            }
            $text = $this->pdo->prepare('INSERT INTO scan_texts (scan_id, page, source, text) VALUES (?, ?, ?, ?)');
            foreach ($texts as $t) {
                $text->execute([$scan['scan_id'], $t['page'], $t['source'], $t['text']]);
            }
        });
    }

    public function find(string $scanId): ?array
    {
        return $this->one('SELECT * FROM scans WHERE scan_id = ?', [$scanId]);
    }

    public function findByTask(string $taskId): ?array
    {
        return $this->one('SELECT * FROM scans WHERE task_id = ?', [$taskId]);
    }

    public function recent(string $clientId, int $limit, ?string $before): array
    {
        if ($before !== null) {
            $cursor = $this->find($before);
            if ($cursor === null || $cursor['client_id'] !== $clientId) {
                return [];
            }
            $stmt = $this->pdo->prepare('SELECT * FROM scans WHERE client_id = ?
                                           AND (created_at < ? OR (created_at = ? AND scan_id < ?))
                                         ORDER BY created_at DESC, scan_id DESC LIMIT ?');
            $stmt->execute([$clientId, $cursor['created_at'], $cursor['created_at'], $before, $limit]);
        } else {
            $stmt = $this->pdo->prepare('SELECT * FROM scans WHERE client_id = ? ORDER BY created_at DESC, scan_id DESC LIMIT ?');
            $stmt->execute([$clientId, $limit]);
        }
        return $stmt->fetchAll();
    }

    public function pages(string $scanId): array
    {
        $stmt = $this->pdo->prepare('SELECT * FROM scan_pages WHERE scan_id = ? ORDER BY page, rendition');
        $stmt->execute([$scanId]);
        return $stmt->fetchAll();
    }

    public function page(string $scanId, int $page, string $rendition): ?array
    {
        return $this->one('SELECT * FROM scan_pages WHERE scan_id = ? AND page = ? AND rendition = ?', [$scanId, $page, $rendition]);
    }

    public function texts(string $scanId, ?int $page): array
    {
        if ($page === null) {
            $stmt = $this->pdo->prepare('SELECT page, source, text FROM scan_texts WHERE scan_id = ? ORDER BY page');
            $stmt->execute([$scanId]);
        } else {
            $stmt = $this->pdo->prepare('SELECT page, source, text FROM scan_texts WHERE scan_id = ? AND page = ?');
            $stmt->execute([$scanId, $page]);
        }
        return $stmt->fetchAll();
    }

    public function activeStart(string $prompt): ?array
    {
        return $this->one("SELECT * FROM tasks WHERE operation = 'scan' AND prompt = ? AND state IN ("
                          . implode(', ', self::ACTIVE) . ') ORDER BY created_at LIMIT 1', [$prompt]);
    }

    public function startsSince(string $clientId, int $since): int
    {
        $stmt = $this->pdo->prepare("SELECT COUNT(*) FROM tasks WHERE operation = 'scan' AND client_id = ? AND created_at >= ?");
        $stmt->execute([$clientId, $since]);
        return (int) $stmt->fetchColumn();
    }

    private function one(string $sql, array $args): ?array
    {
        $stmt = $this->pdo->prepare($sql);
        $stmt->execute($args);
        $row = $stmt->fetch();
        return $row ?: null;
    }
}
