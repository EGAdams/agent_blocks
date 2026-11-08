<?php
declare(strict_types=1);
namespace AgentBridge\Tests;

use AgentBridge\WorkerDownloads;

/** The browser world, with somewhere to keep blobs. */
function downloadWorld(): World
{
    return browserWorld(['scans_dir' => scanDir()]);
}

/** A browser_download task claimed by the worker: [task_id, lease]. */
function claimedDownload(World $w, array $args = ['text' => '0337201.tif']): array
{
    $id = browserCall($w, 'browser_download', $args)['structuredContent']['task_id'];
    list(, $lease) = claimTask($w, $id);
    return [$id, $lease];
}

function putBlob(World $w, string $id, string $bytes): string
{
    $sha = hash('sha256', $bytes);
    eq(200, $w->call('POST', "/worker/downloads/$id/blobs/$sha", $w->worker, $bytes)->status, 'blob upload');
    return $sha;
}

/** What DownloadPublisher sends for one two-page TIFF. */
function tiffManifest(World $w, string $id, string $lease): array
{
    $original = "II*\0" . str_repeat("\x01", 300);
    $pages    = [];
    foreach ([1, 2] as $n) {
        foreach (['preview', 'full'] as $rendition) {
            $png     = "\x89PNG\r\n\x1a\n page $n $rendition";
            $pages[] = ['page' => $n, 'rendition' => $rendition, 'mime' => 'image/png', 'width' => 800, 'height' => 600,
                        'bytes' => strlen($png), 'sha256' => putBlob($w, $id, $png)];
        }
    }
    return ['task_id' => $id, 'lease_token' => $lease, 'files' => [[
        'idx' => 0, 'name' => '0337201.tif', 'original_name' => '0337201.tif',
        'saved_path' => 'C:\\Users\\NewUser\\Downloads\\BrowserRecorder\\0337201.tif', 'mime' => 'image/tiff',
        'bytes' => strlen($original), 'sha256' => putBlob($w, $id, $original), 'original_stored' => true,
        'kind' => 'image', 'page_count' => 2, 'text' => '', 'text_truncated' => false, 'note' => '', 'pages' => $pages]]];
}

test('downloads: the download tools are listed beside browser_download', function () {
    $names = toolNames(downloadWorld());
    ok(in_array('browser_downloaded_files', $names, true) && in_array('browser_view_file', $names, true), 'listed');
});

test('downloads: the worker publishes a file, and the client sees its pages as images', function () {
    $w = downloadWorld();
    list($id, $lease) = claimedDownload($w);
    $r = $w->call('POST', "/worker/downloads/$id", $w->worker, tiffManifest($w, $id, $lease));
    eq(200, $r->status, 'manifest');
    $fileId = $r->json['files'][0]['file_id'];
    eq(WorkerDownloads::fileId($id, 0), $fileId);
    completeTask($w, $id, $lease, ['browser' => 'download', 'ok' => true, 'verified' => true, 'inspectable' => true,
                                   'files' => [['name' => '0337201.tif', 'file_id' => $fileId, 'bytes' => 304]]]);
    $s = browserCall($w, 'browser_get_result', ['task_id' => $id])['structuredContent'];
    eq(true, $s['verified']);
    eq(true, $s['inspectable']);
    ok(strpos($s['next'], $fileId) !== false, 'points at browser_view_file');

    $listed = browserCall($w, 'browser_downloaded_files')['structuredContent']['files'];
    eq(1, count($listed));
    eq('image/tiff', $listed[0]['mime']);
    eq(2, $listed[0]['page_count']);

    $view = browserCall($w, 'browser_view_file', ['file_id' => $fileId, 'page' => 2]);
    eq(false, $view['isError']);
    eq('image', $view['content'][0]['type']);
    eq('image/png', $view['content'][0]['mimeType']);
    eq("\x89PNG\r\n\x1a\n page 2 full", base64_decode($view['content'][0]['data']));
    eq('full', $view['structuredContent']['page']['rendition']);
    eq(true, browserCall($w, 'browser_view_file', ['file_id' => $fileId, 'page' => 3])['isError'], 'no page 3');
    $preview = browserCall($w, 'browser_view_file', ['file_id' => $fileId, 'rendition' => 'preview']);
    eq("\x89PNG\r\n\x1a\n page 1 preview", base64_decode($preview['content'][0]['data']));
    ok(strpos($preview['structuredContent']['next'], 'page 2') !== false, 'says there is a next page');
});

test('downloads: another client cannot list or view the file', function () {
    $w = downloadWorld();
    list($id, $lease) = claimedDownload($w);
    $fileId = $w->call('POST', "/worker/downloads/$id", $w->worker, tiffManifest($w, $id, $lease))->json['files'][0]['file_id'];
    eq([], browserCall($w, 'browser_downloaded_files', [], $w->client2)['structuredContent']['files']);
    eq([], browserCall($w, 'browser_downloaded_files', ['task_id' => $id], $w->client2)['structuredContent']['files']);
    eq(true, browserCall($w, 'browser_view_file', ['file_id' => $fileId], $w->client2)['isError']);
    eq(true, browserCall($w, 'browser_view_file', ['file_id' => '../../etc/passwd'])['isError']);
});

test('downloads: text comes back in chunks', function () {
    $w = downloadWorld();
    list($id, $lease) = claimedDownload($w, ['text' => 'report.csv']);
    $text = str_repeat("a,b\n", 6000);
    $m = ['task_id' => $id, 'lease_token' => $lease, 'files' => [[
        'idx' => 0, 'name' => 'report.csv', 'original_name' => 'report.csv', 'saved_path' => 'C:\\x\\report.csv',
        'mime' => 'text/csv', 'bytes' => strlen($text), 'sha256' => putBlob($w, $id, $text), 'original_stored' => true,
        'kind' => 'text', 'page_count' => 0, 'text' => $text, 'text_truncated' => false, 'note' => '', 'pages' => []]]];
    $fileId = $w->call('POST', "/worker/downloads/$id", $w->worker, $m)->json['files'][0]['file_id'];
    $first  = browserCall($w, 'browser_view_file', ['file_id' => $fileId]);
    eq('text', $first['content'][0]['type'], 'no image for a text file');
    $s = $first['structuredContent'];
    eq(20000, strlen($s['text']));
    eq(true, $s['text_more']);
    $rest = browserCall($w, 'browser_view_file', ['file_id' => $fileId, 'text_offset' => $s['next_text_offset']])['structuredContent'];
    eq(false, $rest['text_more']);
    eq($text, $s['text'] . $rest['text']);
});

test('downloads: the worker cannot publish without the lease, without the blobs, or to another kind of task', function () {
    $w = downloadWorld();
    list($id, $lease) = claimedDownload($w);
    $m = tiffManifest($w, $id, $lease);
    $stale = $m;
    $stale['lease_token'] = str_repeat('z', 40);
    eq(409, $w->call('POST', "/worker/downloads/$id", $w->worker, $stale)->status, 'wrong lease');
    $missing = $m;
    $missing['files'][0]['pages'][0]['sha256'] = str_repeat('0', 64);
    eq(422, $w->call('POST', "/worker/downloads/$id", $w->worker, $missing)->status, 'page never uploaded');
    $html = $m;
    $html['files'][0]['pages'][0]['mime'] = 'text/html';
    eq(422, $w->call('POST', "/worker/downloads/$id", $w->worker, $html)->status, 'pages are images only');
    eq(401, $w->call('POST', "/worker/downloads/$id", null, $m)->status, 'no token');
    eq(403, $w->call('POST', "/worker/downloads/$id", $w->client, $m)->status, 'a client is not the worker');
    eq(422, $w->call('POST', "/worker/downloads/$id/blobs/" . hash('sha256', 'a'), $w->worker, 'b')->status, 'hash checked');

    $read = browserCall($w, 'browser_read')['structuredContent']['task_id'];
    eq(404, $w->call('POST', "/worker/downloads/$read/blobs/" . hash('sha256', 'x'), $w->worker, 'x')->status, 'not a download');
    eq(200, $w->call('POST', "/worker/downloads/$id", $w->worker, $m)->status, 'the real one still goes through');
    eq(200, $w->call('POST', "/worker/downloads/$id", $w->worker, $m)->status, 'a resend replaces, not duplicates');
    eq(1, count(browserCall($w, 'browser_downloaded_files', ['task_id' => $id])['structuredContent']['files']));
});
