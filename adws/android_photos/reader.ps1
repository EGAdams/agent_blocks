# Android Photos -- the Windows half. Reads a USB-connected phone through the Windows shell (WPD/MTP).
#
# READ ONLY: the phone is only ever the SOURCE of a copy. Nothing here writes to, renames, moves or deletes
# anything on the device, and nothing here unlocks it or changes its USB mode.
#
#   reader.ps1 <request file> <answer file>      both JSON, UTF-8
#
#   {"op": "look"}                                            which portable devices are attached, and their storages
#   {"op": "list", "device": D, "folders": ["DCIM/Camera"]}   the files in the permitted folders (name, modified)
#   {"op": "copy", "device": D, "items": [{"key","storage","folder","name"}], "dest": "C:\\..."}
#
# "root" (tests only, set by the adapter's constructor, never by a caller) treats an ordinary folder as the one
# attached device, so the folder walker can be run without a phone.
param([string]$RequestFile, [string]$AnswerFile)
$ErrorActionPreference = 'Stop'

function Answer($value) {
    $json = $value | ConvertTo-Json -Depth 8 -Compress
    [IO.File]::WriteAllText($AnswerFile, $json, (New-Object Text.UTF8Encoding($false)))
}

function FileName($item) {
    # Explorer may hide extensions in .Name; System.FileName does not.
    try { $n = $item.ExtendedProperty('System.FileName'); if ($n) { return [string]$n } } catch {}
    return [string]$item.Name
}

function Child($folder, [string]$name) {
    foreach ($item in $folder.Items()) {
        if ($item.IsFolder -and ($item.Name -ieq $name)) { return $item.GetFolder }
    }
    return $null
}

function Devices($shell, $request) {
    $found = @()
    if ($request.root) {
        $folder = $shell.NameSpace([string]$request.root)
        if ($folder) { $found += ,@{ name = 'Test Phone'; folder = $folder } }
        return ,$found
    }
    $pc = $shell.NameSpace(17)                                   # This PC
    foreach ($item in $pc.Items()) {
        # A portable (MTP/PTP) device has a \\?\usb# path; a drive letter or a card reader does not.
        if ($item.IsFolder -and ([string]$item.Path -match '\\\\\?\\usb#')) {
            $found += ,@{ name = [string]$item.Name; folder = $item.GetFolder }
        }
    }
    return ,$found
}

function Storages($deviceFolder) {
    $out = @()
    foreach ($item in $deviceFolder.Items()) {
        if ($item.IsFolder) { $out += ,@{ name = [string]$item.Name; folder = $item.GetFolder } }
    }
    return ,$out
}

function Walk($storageFolder, [string]$path) {
    $folder = $storageFolder
    foreach ($part in ($path -split '[\\/]' | Where-Object { $_ })) {
        $folder = Child $folder $part
        if (-not $folder) { return $null }
    }
    return $folder
}

function Named($devices, [string]$name) {
    foreach ($d in $devices) { if ($d.name -eq $name) { return $d } }
    return $null
}

try {
    $request = Get-Content -Raw -Encoding UTF8 $RequestFile | ConvertFrom-Json
    $shell   = New-Object -ComObject Shell.Application
    $devices = Devices $shell $request

    if ($request.op -eq 'look') {
        $seen = @()
        foreach ($d in $devices) {
            $names = @()
            foreach ($s in (Storages $d.folder)) { $names += $s.name }
            $seen += ,@{ name = $d.name; storages = @($names) }
        }
        Answer @{ ok = $true; devices = @($seen) }
        exit 0
    }

    $device = Named $devices ([string]$request.device)
    if (-not $device) { Answer @{ ok = $false; cause = 'no_device'; error = 'that device is not attached' }; exit 0 }
    $storages = Storages $device.folder
    if ($storages.Count -eq 0) { Answer @{ ok = $false; cause = 'locked'; error = 'the device shows no storage' }; exit 0 }

    if ($request.op -eq 'list') {
        $files = @(); $folders = @()
        foreach ($s in $storages) {
            foreach ($path in $request.folders) {
                $folder = Walk $s.folder ([string]$path)
                if (-not $folder) { continue }
                $folders += ,@{ storage = $s.name; folder = [string]$path }
                foreach ($item in $folder.Items()) {
                    if ($item.IsFolder) { continue }
                    $modified = ''
                    try { $modified = ([datetime]$item.ModifyDate).ToString('yyyy-MM-ddTHH:mm:ss') } catch {}
                    $files += ,@{ storage = $s.name; folder = [string]$path; name = (FileName $item); modified = $modified }
                }
            }
        }
        Answer @{ ok = $true; folders = @($folders); files = @($files) }
        exit 0
    }

    if ($request.op -eq 'copy') {
        $copied = @()
        $wanted = @{}
        foreach ($i in $request.items) { $wanted["$($i.storage)|$($i.folder)"] += ,$i }
        foreach ($s in $storages) {
            foreach ($group in $wanted.Keys) {
                $first = $wanted[$group][0]
                if ($first.storage -ne $s.name) { continue }
                $folder = Walk $s.folder ([string]$first.folder)
                if (-not $folder) { continue }
                $byName = @{}
                foreach ($item in $folder.Items()) { if (-not $item.IsFolder) { $byName[(FileName $item)] = $item } }
                foreach ($i in $wanted[$group]) {
                    $item = $byName[[string]$i.name]
                    if (-not $item) { $copied += ,@{ key = $i.key; ok = $false; error = 'no longer on the device' }; continue }
                    $into = Join-Path ([string]$request.dest) ([string]$i.key)
                    New-Item -ItemType Directory -Force -Path $into | Out-Null
                    # 4 no progress box, 16 yes to all, 512 no mkdir prompt, 1024 no error box. The phone is the source.
                    $shell.NameSpace($into).CopyHere($item, 1556)
                    $file = $null; $last = -1; $deadline = (Get-Date).AddSeconds(90)
                    while ((Get-Date) -lt $deadline) {
                        Start-Sleep -Milliseconds 250
                        $got = @(Get-ChildItem -LiteralPath $into -File -ErrorAction SilentlyContinue)
                        if ($got.Count -eq 0) { continue }
                        if ($got[0].Length -gt 0 -and $got[0].Length -eq $last) {
                            try { $h = [IO.File]::Open($got[0].FullName, 'Open', 'Read', 'None'); $h.Close(); $file = $got[0]; break } catch {}
                        }
                        $last = $got[0].Length
                    }
                    if ($file) { $copied += ,@{ key = $i.key; ok = $true; path = $file.FullName; bytes = $file.Length } }
                    else       { $copied += ,@{ key = $i.key; ok = $false; error = 'the copy did not finish' } }
                }
            }
        }
        Answer @{ ok = $true; copied = @($copied) }
        exit 0
    }
    Answer @{ ok = $false; cause = 'bad_request'; error = 'unknown op' }
} catch {
    Answer @{ ok = $false; cause = 'reader_failed'; error = [string]$_.Exception.Message }
}
