<?php
declare(strict_types=1);

namespace AgentBridge;

/**
 * The worker's side: page and original bytes go up one blob at a time (by
 * sha256), then a manifest binds them to the scan and its task. The manifest
 * is re-posted as Mazda's verdict arrives, and only while the worker still
 * holds the task's lease.
 */
final class WorkerScans
{
    const STATUSES  = ['queued', 'scanning', 'captured', 'failed', 'cancelled', 'timed_out', 'interrupted'];
    const MIMES     = ['image/jpeg', 'image/png', 'application/pdf'];
    const MAX_RECORD = 32768;
    const MAX_PAGES  = 200;
    const MAX_TEXT   = 204800;

    /** @var ITaskRepository */   private $tasks;
    /** @var ITaskLeaseManager */ private $leases;
    /** @var IScanStore */        private $scans;
    /** @var IBlobStore */        private $blobs;
    /** @var IAuditLogger */      private $audit;
    /** @var int */               private $leaseSeconds;

    public function __construct(ITaskRepository $tasks, ITaskLeaseManager $leases, IScanStore $scans, IBlobStore $blobs,
                                IAuditLogger $audit, int $leaseSeconds)
    {
        $this->tasks        = $tasks;
        $this->leases       = $leases;
        $this->scans        = $scans;
        $this->blobs        = $blobs;
        $this->audit        = $audit;
        $this->leaseSeconds = $leaseSeconds;
    }

    public function blob(Principal $who, string $scanId, string $sha256, string $bytes): array
    {
        if ($bytes === '') {
            throw new BridgeError(422, 'empty_blob', 'no bytes were sent');
        }
        $this->blobs->put($sha256, $bytes);
        $this->audit->record($who->id, 'scan_blob', null, 'stored', ['scan_id' => $scanId, 'bytes' => strlen($bytes)]);
        return ['sha256' => $sha256, 'stored' => true, 'bytes' => strlen($bytes)];
    }

    public function manifest(Principal $who, string $scanId, array $body): array
    {
        $taskId = isset($body['task_id']) && is_string($body['task_id']) ? $body['task_id'] : '';
        $lease  = isset($body['lease_token']) && is_string($body['lease_token']) ? $body['lease_token'] : '';
        if (!preg_match('/^t_[0-9a-f]{24}$/', $taskId) || !preg_match('/^[A-Za-z0-9_-]{20,200}$/', $lease)) {
            throw new BridgeError(422, 'invalid_field', 'task_id and lease_token are required');
        }
        $task = $this->tasks->find($taskId);
        if ($task === null || $task['operation'] !== 'scan') {
            throw new BridgeError(404, 'not_found', 'no such scan task');
        }
        $scanner = substr((string) $task['prompt'], 8);
        $record  = isset($body['record']) && is_array($body['record']) ? $body['record'] : null;
        $json    = $record === null ? false : json_encode($record, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
        if ($json === false || strlen($json) > self::MAX_RECORD || substr($json, 0, 1) !== '{') {
            throw new BridgeError(422, 'invalid_record', 'record must be a JSON object under ' . self::MAX_RECORD . ' bytes');
        }
        if (($record['scan_id'] ?? null) !== $scanId || ($record['scanner'] ?? null) !== $scanner
            || !in_array($record['status'] ?? null, self::STATUSES, true)) {
            throw new BridgeError(422, 'invalid_record', 'record must name this scan, the task\'s scanner, and a known status');
        }
        $original = $this->original(isset($body['original']) && is_array($body['original']) ? $body['original'] : []);
        $pages    = $this->pages(isset($body['pages']) && is_array($body['pages']) ? $body['pages'] : []);
        $texts    = $this->texts(isset($body['texts']) && is_array($body['texts']) ? $body['texts'] : []);

        $bound = $this->scans->find($scanId);
        $other = $this->scans->findByTask($taskId);
        if (($bound !== null && $bound['task_id'] !== $taskId) || ($other !== null && $other['scan_id'] !== $scanId)) {
            throw new BridgeError(409, 'scan_conflict', 'that scan or task is already bound to another');
        }
        if ($this->leases->heartbeat($taskId, $lease, $this->leaseSeconds) === null) {
            throw new BridgeError(409, 'lease_invalid', 'the lease is expired or not yours');
        }
        $numbers = array_unique(array_map(function ($p) { return $p['page']; }, $pages));
        $this->scans->save([
            'scan_id' => $scanId, 'task_id' => $taskId, 'client_id' => $task['client_id'], 'scanner' => $scanner,
            'status' => $record['status'], 'record' => $json,
            'original_sha256' => $original['sha256'], 'original_mime' => $original['mime'],
            'original_bytes' => $original['bytes'], 'original_stored' => $original['stored'],
            'page_count' => isset($record['artifact']['page_count']) && is_int($record['artifact']['page_count'])
                ? $record['artifact']['page_count'] : count($numbers),
        ], $pages, $texts);
        $this->audit->record($who->id, 'scan_manifest', $taskId, (string) $record['status'],
                             ['scan_id' => $scanId, 'pages' => count($pages), 'texts' => count($texts)]);
        return ['scan_id' => $scanId, 'task_id' => $taskId, 'saved' => true];
    }

    private function original(array $o): array
    {
        if (!$o) {
            return ['sha256' => null, 'mime' => null, 'bytes' => null, 'stored' => false];
        }
        $sha = isset($o['sha256']) && is_string($o['sha256']) ? $o['sha256'] : '';
        if (!preg_match(FileBlobStore::SHA, $sha) || !in_array($o['mime'] ?? null, self::MIMES, true)
            || !isset($o['bytes']) || !is_int($o['bytes']) || $o['bytes'] < 1) {
            throw new BridgeError(422, 'invalid_original', 'original needs a sha256, a known mime and a byte count');
        }
        $stored = !empty($o['stored']);
        if ($stored && !$this->blobs->has($sha)) {
            throw new BridgeError(422, 'missing_blob', 'upload the original before naming it stored');
        }
        return ['sha256' => $sha, 'mime' => $o['mime'], 'bytes' => $o['bytes'], 'stored' => $stored];
    }

    private function pages(array $list): array
    {
        if (count($list) > self::MAX_PAGES) {
            throw new BridgeError(422, 'too_many_pages', 'at most ' . self::MAX_PAGES . ' page renditions');
        }
        $out = [];
        foreach ($list as $p) {
            $ok = is_array($p) && isset($p['page'], $p['width'], $p['height'], $p['bytes'])
                && is_int($p['page']) && $p['page'] >= 1 && $p['page'] <= 500
                && in_array($p['rendition'] ?? null, ['preview', 'full'], true)
                && ($p['mime'] ?? null) === 'image/jpeg'
                && is_string($p['sha256'] ?? null) && preg_match(FileBlobStore::SHA, $p['sha256'])
                && is_int($p['width']) && is_int($p['height']) && is_int($p['bytes']);
            if (!$ok) {
                throw new BridgeError(422, 'invalid_page', 'each page needs page, rendition, image/jpeg, sha256, width, height, bytes');
            }
            if (!$this->blobs->has($p['sha256'])) {
                throw new BridgeError(422, 'missing_blob', 'upload page ' . $p['page'] . ' before the manifest');
            }
            $key = $p['page'] . '/' . $p['rendition'];
            $out[$key] = ['page' => $p['page'], 'rendition' => $p['rendition'], 'sha256' => $p['sha256'], 'mime' => 'image/jpeg',
                          'width' => $p['width'], 'height' => $p['height'], 'bytes' => $p['bytes']];
        }
        return array_values($out);
    }

    private function texts(array $list): array
    {
        if (count($list) > self::MAX_PAGES) {
            throw new BridgeError(422, 'too_many_texts', 'at most ' . self::MAX_PAGES . ' text pages');
        }
        $out   = [];
        $total = 0;
        foreach ($list as $t) {
            $ok = is_array($t) && isset($t['page'], $t['source'], $t['text']) && is_int($t['page']) && $t['page'] >= 1
                && is_string($t['source']) && preg_match('/^[a-z_]{1,32}$/', $t['source'])
                && is_string($t['text']) && preg_match('//u', $t['text']);
            if (!$ok) {
                throw new BridgeError(422, 'invalid_text', 'each text needs page, a source name, and UTF-8 text');
            }
            $total += strlen($t['text']);
            if ($total > self::MAX_TEXT) {
                throw new BridgeError(422, 'text_too_large', 'at most ' . self::MAX_TEXT . ' bytes of text per scan');
            }
            $out[$t['page']] = ['page' => $t['page'], 'source' => $t['source'], 'text' => $t['text']];
        }
        return array_values($out);
    }
}
