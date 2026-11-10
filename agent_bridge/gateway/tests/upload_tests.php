<?php
declare(strict_types=1);
namespace AgentBridge\Tests;

use AgentBridge\BridgeError;
use AgentBridge\IUrlFetcher;

/** A fetcher that answers from a table instead of the network, and records what it was asked for. */
final class FakeFetcher implements IUrlFetcher
{
    public $asked = [];
    private $pages;

    public function __construct(array $pages) { $this->pages = $pages; }

    public function fetch(string $url, int $maxBytes): array
    {
        $this->asked[] = $url;
        if (!isset($this->pages[$url])) {
            throw new BridgeError(502, 'fetch_failed', 'the link answered HTTP 404');
        }
        return [$this->pages[$url], (string) parse_url($url, PHP_URL_HOST)];
    }
}

const UPLOAD_TOOLS = ['notes_upload_begin', 'notes_upload_chunk', 'notes_upload_finish', 'notes_upload_from_url'];

function uploadPdf(string $words = 'synthetic deed'): string
{
    return "%PDF-1.4\n1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n2 0 obj << /Type /Pages /Count 1 >> endobj\n"
         . "3 0 obj << /Type /Page /Parent 2 0 R >> endobj\n% $words\ntrailer << /Root 1 0 R >>\n%%EOF\n";
}

function uploadWorld(array $overrides = []): World
{
    return notesWorld($overrides + ['scans_dir' => scanDir()]);
}

/** Uploads $pdf in chunks of $size bytes as $token's client; returns the finish answer. */
function uploadInChunks(World $w, string $pdf, int $size = 64, ?string $token = null): array
{
    $begin = notesCall($w, 'notes_upload_begin', ['name' => 'Deed_REVISED.pdf', 'bytes' => strlen($pdf),
                                                  'sha256' => hash('sha256', $pdf)], $token);
    eq(false, $begin['isError'], 'begin: ' . json_encode($begin));
    $id = $begin['structuredContent']['upload_id'];
    for ($at = 0; $at < strlen($pdf); $at += $size) {
        $part = notesCall($w, 'notes_upload_chunk', ['upload_id' => $id, 'offset' => $at,
                                                     'data_base64' => base64_encode(substr($pdf, $at, $size))], $token);
        eq(false, $part['isError'], 'chunk at ' . $at . ': ' . json_encode($part));
    }
    return notesCall($w, 'notes_upload_finish', ['upload_id' => $id], $token);
}

test('uploads: the four upload tools and notes_attach_pdf are offered only when attaching is', function () {
    $names = toolNames(uploadWorld());
    foreach (array_merge(UPLOAD_TOOLS, ['notes_attach_pdf']) as $tool) {
        ok(in_array($tool, $names, true), "$tool listed");
    }
    $readOnly = toolNames(uploadWorld(['allowed_operations' => ['inspect', 'explain', 'search_memory'],
                                       'allowed_modes' => ['read_only']]));
    foreach (array_merge(UPLOAD_TOOLS, ['notes_attach_pdf']) as $tool) {
        ok(!in_array($tool, $readOnly, true), "$tool hidden read-only");
    }
    $w = uploadWorld(['allowed_operations' => ['inspect', 'explain', 'search_memory'], 'allowed_modes' => ['read_only']]);
    eq(true, notesCall($w, 'notes_upload_begin', ['name' => 'a.pdf', 'bytes' => 10, 'sha256' => str_repeat('a', 64)])['isError'],
       'a call is refused when the tool is not offered');
});

test('uploads: begin, chunks in order, finish stores a whole PDF', function () {
    $w   = uploadWorld();
    $pdf = uploadPdf();
    $out = uploadInChunks($w, $pdf);
    eq(false, $out['isError'], json_encode($out));
    $s = $out['structuredContent'];
    eq(true, $s['ready']);
    eq(hash('sha256', $pdf), $s['sha256']);
    eq(strlen($pdf), $s['bytes']);
    eq(1, $s['pages']);
    eq('Deed_REVISED.pdf', $s['name']);
    $again = notesCall($w, 'notes_upload_finish', ['upload_id' => $s['upload_id']]);
    eq(false, $again['structuredContent']['stored'], 'a second finish is an answer, not a second store');
});

test('uploads: an offset gap, a short upload, a wrong hash and a non-PDF are refused', function () {
    $w     = uploadWorld();
    $pdf   = uploadPdf();
    $begin = notesCall($w, 'notes_upload_begin', ['name' => 'x.pdf', 'bytes' => strlen($pdf), 'sha256' => hash('sha256', $pdf)]);
    $id    = $begin['structuredContent']['upload_id'];
    $gap   = notesCall($w, 'notes_upload_chunk', ['upload_id' => $id, 'offset' => 10, 'data_base64' => base64_encode('abc')]);
    ok($gap['isError'] && strpos($gap['content'][0]['text'], 'offset_gap') === 0, 'gap: ' . json_encode($gap));
    notesCall($w, 'notes_upload_chunk', ['upload_id' => $id, 'offset' => 0, 'data_base64' => base64_encode(substr($pdf, 0, 20))]);
    $short = notesCall($w, 'notes_upload_finish', ['upload_id' => $id]);
    ok($short['isError'] && strpos($short['content'][0]['text'], 'incomplete') === 0, 'short: ' . json_encode($short));

    $other = uploadPdf('different words');
    $wrong = notesCall($w, 'notes_upload_begin', ['name' => 'y.pdf', 'bytes' => strlen($other), 'sha256' => hash('sha256', $pdf)]);
    $wid   = $wrong['structuredContent']['upload_id'];
    notesCall($w, 'notes_upload_chunk', ['upload_id' => $wid, 'offset' => 0, 'data_base64' => base64_encode($other)]);
    $hash = notesCall($w, 'notes_upload_finish', ['upload_id' => $wid]);
    ok($hash['isError'] && strpos($hash['content'][0]['text'], 'hash_mismatch') === 0, 'hash: ' . json_encode($hash));

    $text = 'just some words, not a PDF';
    $not  = notesCall($w, 'notes_upload_begin', ['name' => 'z.pdf', 'bytes' => strlen($text), 'sha256' => hash('sha256', $text)]);
    $nid  = $not['structuredContent']['upload_id'];
    notesCall($w, 'notes_upload_chunk', ['upload_id' => $nid, 'offset' => 0, 'data_base64' => base64_encode($text)]);
    $bad = notesCall($w, 'notes_upload_finish', ['upload_id' => $nid]);
    ok($bad['isError'] && strpos($bad['content'][0]['text'], 'not_a_pdf') === 0, 'not a pdf: ' . json_encode($bad));

    $big = notesCall($w, 'notes_upload_begin', ['name' => 'big.pdf', 'bytes' => 10485761, 'sha256' => str_repeat('a', 64)]);
    eq(true, $big['isError'], 'over 10 MiB refused up front');
});

test('uploads: another client can neither see, finish nor attach an upload', function () {
    $w  = uploadWorld();
    $id = uploadInChunks($w, uploadPdf())['structuredContent']['upload_id'];
    $finish = notesCall($w, 'notes_upload_finish', ['upload_id' => $id], $w->client2);
    ok($finish['isError'] && strpos($finish['content'][0]['text'], 'not_found') === 0, 'finish by client2');
    $attach = notesCall($w, 'notes_attach_pdf', ['note' => NOTES_NOTE, 'upload_id' => $id, 'title' => 'Deed'], $w->client2);
    eq(true, $attach['isError'], 'attach by client2: ' . json_encode($attach));
});

test('uploads: from_url fetches a public link, and a sandbox: path is refused without fetching', function () {
    $pdf     = uploadPdf();
    $fetcher = new FakeFetcher(['https://files.example.com/x/Deed_REVISED.pdf' => $pdf]);
    $w       = uploadWorld(['upload_fetcher' => $fetcher]);
    $out = notesCall($w, 'notes_upload_from_url', ['file' => ['download_url' => 'https://files.example.com/x/Deed_REVISED.pdf',
                                                              'file_id' => 'file-abc'], 'sha256' => hash('sha256', $pdf)]);
    eq(false, $out['isError'], json_encode($out));
    eq(true, $out['structuredContent']['ready']);
    eq(hash('sha256', $pdf), $out['structuredContent']['sha256']);

    $sandbox = notesCall($w, 'notes_upload_from_url', ['url' => 'sandbox:/mnt/data/Personal_Representative_Deed.pdf']);
    ok($sandbox['isError'] && strpos($sandbox['content'][0]['text'], 'bad_url') === 0, 'sandbox: ' . json_encode($sandbox));
    $local = notesCall($w, 'notes_upload_from_url', ['url' => '/etc/passwd']);
    eq(true, $local['isError'], 'local path');
    eq(1, count($fetcher->asked), 'neither refused link was fetched');

    $mismatch = notesCall($w, 'notes_upload_from_url', ['url' => 'https://files.example.com/x/Deed_REVISED.pdf',
                                                        'sha256' => str_repeat('b', 64)]);
    ok($mismatch['isError'] && strpos($mismatch['content'][0]['text'], 'hash_mismatch') === 0, 'mismatch');
});

test('uploads: notes_attach_pdf carries the upload\'s hash, size and name to the worker, and needs it finished', function () {
    $w   = uploadWorld();
    $pdf = uploadPdf();
    $id  = uploadInChunks($w, $pdf)['structuredContent']['upload_id'];
    $out = notesCall($w, 'notes_attach_pdf', ['note' => NOTES_NOTE, 'upload_id' => $id,
                                              'title' => 'Latest Printable Deed PDF']);
    eq(false, $out['isError'], json_encode($out));
    list($prompt) = claimTask($w, $out['structuredContent']['task_id']);
    $req = json_decode($prompt, true);
    eq('review_attach_pdf', $req['notes']);
    eq($id, $req['upload_id']);
    eq(hash('sha256', $pdf), $req['upload_sha256']);
    eq(strlen($pdf), $req['upload_bytes']);
    eq('Deed_REVISED.pdf', $req['upload_name']);

    $open = notesCall($w, 'notes_upload_begin', ['name' => 'o.pdf', 'bytes' => strlen($pdf), 'sha256' => hash('sha256', $pdf)]);
    $not  = notesCall($w, 'notes_attach_pdf', ['note' => NOTES_NOTE, 'upload_id' => $open['structuredContent']['upload_id'],
                                               'title' => 'x']);
    eq(true, $not['isError'], 'an unfinished upload cannot be attached');
});

test('uploads: the worker gets the bytes only for that running task, with its lease, by POST', function () {
    $w   = uploadWorld();
    $pdf = uploadPdf();
    $id  = uploadInChunks($w, $pdf)['structuredContent']['upload_id'];
    $tid = notesCall($w, 'notes_attach_pdf', ['note' => NOTES_NOTE, 'upload_id' => $id, 'title' => 'Deed'])['structuredContent']['task_id'];
    eq(404, $w->call('POST', "/worker/tasks/$tid/upload", $w->worker, ['lease_token' => str_repeat('x', 30)])->status,
       'not running yet');
    list(, $lease) = claimTask($w, $tid);
    eq(405, $w->call('GET', "/worker/tasks/$tid/upload", $w->worker, null, ['lease_token' => $lease])->status, 'GET');
    eq(409, $w->call('POST', "/worker/tasks/$tid/upload", $w->worker, ['lease_token' => str_repeat('x', 30)])->status,
       'wrong lease');
    ok(in_array($w->call('POST', "/worker/tasks/$tid/upload", $w->client, ['lease_token' => $lease])->status, [401, 403], true),
       'a client token cannot fetch');
    $got = $w->call('POST', "/worker/tasks/$tid/upload", $w->worker, ['lease_token' => $lease]);
    eq(200, $got->status, json_encode($got->json));
    eq($pdf, base64_decode($got->json['pdf_base64'], true));
    eq(hash('sha256', $pdf), $got->json['sha256']);
    eq($id, $got->json['upload_id']);

    $plain = notesCall($w, 'notes_inspect', ['note' => NOTES_NOTE])['structuredContent']['task_id'];
    list(, $plainLease) = claimTask($w, $plain);
    eq(404, $w->call('POST', "/worker/tasks/$plain/upload", $w->worker, ['lease_token' => $plainLease])->status,
       'not an attach task');
});
