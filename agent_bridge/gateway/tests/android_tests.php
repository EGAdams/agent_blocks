<?php
declare(strict_types=1);
namespace AgentBridge\Tests;

/** A gateway with the Android Photos agent (inspect only), somewhere to keep blobs, and no waiting. */
function androidWorld(array $overrides = []): World
{
    return new World($overrides + [
        'allowed_operations'   => ['inspect', 'explain', 'search_memory', 'implement'],
        'allowed_modes'        => ['read_only', 'workspace_write'],
        'android_wait_seconds' => 0,
        'browser_wait_seconds' => 0,
        'scans_dir'            => scanDir(),
        'agents' => [
            ['name' => 'codex', 'display_name' => 'Codex', 'description' => 'reads code',
             'operations' => ['inspect', 'explain', 'search_memory'], 'workspaces' => ['agent_blocks']],
            ['name' => 'android-photos', 'display_name' => 'Android Photos', 'description' => 'EG\'s phone, read-only',
             'operations' => ['inspect'], 'workspaces' => ['android-photos']],
            ['name' => 'browser', 'display_name' => 'Browser Control', 'description' => 'drives Chrome',
             'operations' => ['inspect', 'implement'], 'workspaces' => ['browser'], 'write_workspaces' => ['browser']],
        ],
    ]);
}

function androidCall(World $w, string $tool, array $args = [], ?string $token = null): array
{
    return $w->mcp('tools/call', ['name' => $tool, 'arguments' => $args ?: new \stdClass()], 1, $token)->json['result'];
}

const ANDROID_TOOLS = ['android_status', 'android_list_recent_photos', 'android_get_photo', 'android_get_thumbnail',
                       'android_get_result'];

/** What the worker's DownloadPublisher sends for $n thumbnails (or, with $full, one photograph with both sizes). */
function photoManifest(World $w, string $id, string $lease, int $n, bool $full = false): array
{
    $files = [];
    for ($i = 0; $i < $n; $i++) {
        $preview = "\xff\xd8\xff preview " . ($i + 1);
        $pages   = [['page' => 1, 'rendition' => 'preview', 'mime' => 'image/jpeg', 'width' => 512, 'height' => 384,
                     'bytes' => strlen($preview), 'sha256' => putBlob($w, $id, $preview)]];
        $main    = $preview;
        if ($full) {
            $big     = "\xff\xd8\xff full " . ($i + 1);
            $pages[] = ['page' => 1, 'rendition' => 'full', 'mime' => 'image/jpeg', 'width' => 2048, 'height' => 1536,
                        'bytes' => strlen($big), 'sha256' => putBlob($w, $id, $big)];
            $main    = "\xff\xd8\xff the original " . ($i + 1);
        }
        $files[] = ['idx' => $i, 'name' => 'IMG_2026111' . $i . '_093015.jpg', 'original_name' => 'IMG.jpg',
                    'saved_path' => '', 'mime' => 'image/jpeg', 'bytes' => strlen($main), 'sha256' => putBlob($w, $id, $main),
                    'original_stored' => true, 'kind' => 'image', 'page_count' => 1, 'text' => '', 'text_truncated' => false,
                    'note' => '', 'pages' => $pages];
    }
    return ['task_id' => $id, 'lease_token' => $lease, 'files' => $files];
}

/** The worker's half of one android task: claim, publish, complete. Returns the claimed prompt. */
function workAndroid(World $w, string $id, string $op, int $n, bool $full = false): array
{
    list($prompt, $lease) = claimTask($w, $id);
    $r = $w->call('POST', "/worker/downloads/$id", $w->worker, photoManifest($w, $id, $lease, $n, $full));
    eq(200, $r->status, 'manifest');
    $photos = [];
    foreach ($r->json['files'] as $i => $f) {
        $photos[] = ['number' => $i + 1, 'name' => $f['name'], 'taken_at' => '2026-11-1' . $i . 'T09:30:15',
                     'taken_source' => 'exif', 'file_id' => $f['file_id']];
    }
    completeTask($w, $id, $lease, ['android' => $op, 'ok' => true, 'verified' => true, 'state' => 'ready',
                                   'device' => 'Pixel 8', 'photos' => $photos, 'inspectable' => true]);
    return (array) json_decode($prompt, true);
}

function imagesIn(array $answer): array
{
    return array_values(array_filter($answer['content'], function ($c) { return $c['type'] === 'image'; }));
}

test('android: the five tools are listed, read-only, beside the task tools', function () {
    $w     = androidWorld();
    $names = toolNames($w);
    foreach (ANDROID_TOOLS as $tool) {
        ok(in_array($tool, $names, true), "$tool listed");
    }
    ok(in_array('submit_agent_task', $names, true), 'task tools still listed');
    foreach ($w->mcp('tools/list', [], 1)->json['result']['tools'] as $t) {
        if (strpos($t['name'], 'android_') === 0) {
            eq(true, $t['annotations']['readOnlyHint'], $t['name'] . ' is read-only');
        }
    }
});

test('android: no agent in the registry means no tools', function () {
    $names = toolNames(browserWorld());
    eq([], array_values(array_filter($names, function ($n) { return strpos($n, 'android_') === 0; })));
});

test('android: a listing defaults to fifteen and comes back as fifteen thumbnails, newest first', function () {
    $w    = androidWorld();
    $sent = androidCall($w, 'android_list_recent_photos');
    eq(false, $sent['isError']);
    eq(false, $sent['structuredContent']['ready']);
    $id   = $sent['structuredContent']['task_id'];
    $req  = workAndroid($w, $id, 'list_recent_photos', 15);
    eq(['android' => 'list_recent_photos', 'count' => 15], $req);

    $got = androidCall($w, 'android_get_result', ['task_id' => $id]);
    eq(true, $got['structuredContent']['verified']);
    eq(15, count($got['structuredContent']['photos']));
    $images = imagesIn($got);
    eq(15, count($images));
    eq('image/jpeg', $images[0]['mimeType']);
    eq("\xff\xd8\xff preview 1", base64_decode($images[0]['data']));
    eq("\xff\xd8\xff preview 15", base64_decode($images[14]['data']));
    ok(strpos(json_encode($got['content'][1]), 'Photo 1') !== false, 'each picture is labelled with its number');
});

test('android: "the five newest" asks the machine for five', function () {
    $w  = androidWorld();
    $id = androidCall($w, 'android_list_recent_photos', ['count' => 5])['structuredContent']['task_id'];
    eq(5, workAndroid($w, $id, 'list_recent_photos', 5)['count']);
    eq(5, count(imagesIn(androidCall($w, 'android_get_result', ['task_id' => $id]))));
});

test('android: "open picture number three" returns the photograph, not its thumbnail', function () {
    $w   = androidWorld();
    $id  = androidCall($w, 'android_get_photo', ['number' => 3])['structuredContent']['task_id'];
    eq(['android' => 'get_photo', 'number' => 3], workAndroid($w, $id, 'get_photo', 1, true));
    $got = androidCall($w, 'android_get_result', ['task_id' => $id]);
    $images = imagesIn($got);
    eq(1, count($images));
    eq("\xff\xd8\xff full 1", base64_decode($images[0]['data']));
    eq('2026-11-10T09:30:15', $got['structuredContent']['photos'][0]['taken_at']);
});

test('android: a thumbnail request returns the small picture', function () {
    $w  = androidWorld();
    $id = androidCall($w, 'android_get_thumbnail', ['name' => 'IMG_20261110_093015.jpg'])['structuredContent']['task_id'];
    eq(['android' => 'get_thumbnail', 'name' => 'IMG_20261110_093015.jpg'], workAndroid($w, $id, 'get_thumbnail', 1, true));
    eq("\xff\xd8\xff preview 1", base64_decode(imagesIn(androidCall($w, 'android_get_result', ['task_id' => $id]))[0]['data']));
});

test('android: bad requests are refused before a task is queued', function () {
    $w = androidWorld();
    foreach ([['android_list_recent_photos', ['count' => 0]], ['android_list_recent_photos', ['count' => 21]],
              ['android_list_recent_photos', ['count' => 'all']], ['android_get_photo', []],
              ['android_get_photo', ['number' => 2, 'name' => 'a.jpg']], ['android_get_photo', ['name' => '../../secret.jpg']],
              ['android_get_photo', ['name' => 'DCIM\\Camera\\a.jpg']], ['android_get_thumbnail', ['name' => '.hidden.jpg']],
              ['android_get_thumbnail', ['number' => 99]]] as $case) {
        $r = androidCall($w, $case[0], $case[1]);
        eq(true, $r['isError'], $case[0] . ' ' . json_encode($case[1]));
        ok(strpos($r['content'][0]['text'], 'invalid_argument') === 0, 'invalid_argument');
    }
    eq(0, (int) $w->pdo->query('SELECT COUNT(*) FROM tasks')->fetchColumn());
});

test('android: a locked phone is answered with what EG must do, and no picture', function () {
    $w  = androidWorld();
    $id = androidCall($w, 'android_list_recent_photos')['structuredContent']['task_id'];
    list(, $lease) = claimTask($w, $id);
    completeTask($w, $id, $lease, ['android' => 'list_recent_photos', 'ok' => false, 'verified' => false, 'state' => 'locked',
                                   'device' => 'Pixel 8', 'next_step' => 'Unlock the phone.', 'photos' => []], false);
    $got = androidCall($w, 'android_get_result', ['task_id' => $id]);
    eq(false, $got['structuredContent']['verified']);
    eq('locked', $got['structuredContent']['state']);
    ok(strpos($got['structuredContent']['next'], 'unlock') !== false, 'says to unlock');
    ok(strpos($got['structuredContent']['next'], 'Do not try anything else') !== false, 'and nothing else');
    eq([], imagesIn($got));
});

test('android: another client can neither read the task nor get its pictures', function () {
    $w  = androidWorld();
    $id = androidCall($w, 'android_list_recent_photos', ['count' => 2])['structuredContent']['task_id'];
    workAndroid($w, $id, 'list_recent_photos', 2);
    $other = androidCall($w, 'android_get_result', ['task_id' => $id], $w->client2);
    eq(true, $other['isError']);
    eq([], imagesIn($other));
    $fileId = androidCall($w, 'android_get_result', ['task_id' => $id])['structuredContent']['photos'][0]['file_id'];
    eq(true, browserCall($w, 'browser_view_file', ['file_id' => $fileId], $w->client2)['isError']);
});

test('android: only the three picture operations may publish files; status may not, nor may another agent', function () {
    $w  = androidWorld();
    $id = androidCall($w, 'android_status')['structuredContent']['task_id'];
    list(, $lease) = claimTask($w, $id);
    $blob = hash('sha256', 'x');
    eq(404, $w->call('POST', "/worker/downloads/$id/blobs/$blob", $w->worker, 'x')->status, 'status publishes nothing');
    completeTask($w, $id, $lease, ['android' => 'status', 'ok' => true, 'verified' => true, 'state' => 'ready',
                                   'device' => 'Pixel 8', 'folders' => ['Internal storage/DCIM/Camera'], 'photo_count' => 412]);
    $got = androidCall($w, 'android_get_result', ['task_id' => $id])['structuredContent'];
    eq(412, $got['photo_count']);
    eq(true, $got['verified']);

    $plain = $w->submit(['prompt' => '{"android":"get_photo","number":1}']);
    eq(404, $w->call('POST', '/worker/downloads/' . $plain->json['task_id'] . "/blobs/$blob", $w->worker, 'x')->status,
       'a codex task that merely says android publishes nothing');
});

test('android: an answer too big for one reply names the pictures left out', function () {
    $w   = androidWorld();
    $id  = androidCall($w, 'android_list_recent_photos', ['count' => 2])['structuredContent']['task_id'];
    list(, $lease) = claimTask($w, $id);
    $photos = [];
    $files  = [];
    foreach ([0, 1] as $i) {
        $big     = "\xff\xd8\xff" . str_repeat(chr(65 + $i), 3600000);
        $sha     = putBlob($w, $id, $big);
        $files[] = ['idx' => $i, 'name' => "p$i.jpg", 'original_name' => "p$i.jpg", 'saved_path' => '', 'mime' => 'image/jpeg',
                    'bytes' => strlen($big), 'sha256' => $sha, 'original_stored' => true, 'kind' => 'image', 'page_count' => 1,
                    'text' => '', 'text_truncated' => false, 'note' => '',
                    'pages' => [['page' => 1, 'rendition' => 'preview', 'mime' => 'image/jpeg', 'width' => 512, 'height' => 384,
                                 'bytes' => strlen($big), 'sha256' => $sha]]];
    }
    $r = $w->call('POST', "/worker/downloads/$id", $w->worker, ['task_id' => $id, 'lease_token' => $lease, 'files' => $files]);
    eq(200, $r->status);
    foreach ($r->json['files'] as $i => $f) {
        $photos[] = ['number' => $i + 1, 'name' => $f['name'], 'file_id' => $f['file_id']];
    }
    completeTask($w, $id, $lease, ['android' => 'list_recent_photos', 'ok' => true, 'verified' => true, 'state' => 'ready',
                                   'photos' => $photos]);
    $got = androidCall($w, 'android_get_result', ['task_id' => $id]);
    eq(1, count(imagesIn($got)));
    $last = end($got['content']);
    ok(strpos($last['text'], 'photo 2') !== false && strpos($last['text'], 'android_get_thumbnail') !== false, 'names photo 2');
});
