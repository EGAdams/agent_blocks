# Android Photos — design

Read-only access to the photographs on EG's Android phone, plugged into the Windows computer (SQMC) by USB, so
that ChatGPT can show them: the newest fifteen as thumbnails, any one of them at full size. No USB debugging, no
root, no app on the phone -- the phone is read the way File Explorer reads it (Windows Portable Devices / MTP,
through the Windows shell).

```
uv run adws/adw_android_photos.py <operation> [flags]       # all four, one harness
uv run adws/adw_android_status.py                            # each one on its own
uv run adws/adw_android_list_recent_photos.py [--count 15]
uv run adws/adw_android_get_photo.py     --number 3 | --name IMG_....jpg
uv run adws/adw_android_get_thumbnail.py --number 3 | --name IMG_....jpg
uv run adws/adw_android_authorize.py --device "<the phone's name>"   # EG, once, on the machine itself
```

## The road a picture travels

```
phone --USB/MTP--> Windows shell --reader.ps1--> staging folder --> WSL cache --> worker --> gateway --> ChatGPT
```

| caller | path |
|---|---|
| a shell / Claude Code | the ADWs above |
| the Bridge worker | agent `android-photos` (`lancedb_memory/agent_bridge_worker/android_photos_agent.py`), each task a subprocess of the ADW; on only when `~/.config/android_photos/enabled` exists |
| ChatGPT | MCP tools `android_*` (`agent_bridge/gateway/src/AndroidTools.php`, a subclass of `AgentTaskTools`) → Bridge task → the worker above |

The worker publishes each picture through the existing `DownloadPublisher` and the gateway's download store --
the same road a `browser_download` file travels -- and `AndroidTools` appends each one to the tool answer as MCP
image content. Nothing new was built to carry bytes.

## Parts, one per file

| part | where | job |
|---|---|---|
| interfaces | `interfaces.py` | `IPhoneReader` (`look`, `list`, `copy` -- and nothing else), `IPhotoRenderer`, `ICaptureClock`, `ILastListing` |
| models | `models.py` | every value that crosses a boundary, Pydantic |
| Adapter | `powershell_reader.py` + `reader.ps1` | `IPhoneReader` over Windows PowerShell; the request travels as a JSON file, never on the command line |
| Adapter | `rendering.py` `PillowRenderer` | an upright JPEG at `preview` (512 px) or `full` (2048 px); HEIC through `pillow-heif` |
| Strategy | `capture_time.py` | when a picture was taken: EXIF, else the file name, else the file date |
| operations | `operations.py` | `PhoneGate`, `RecentPhotos`, `OnePhoto` -- written against the interfaces only |
| cache | `cache.py` | the temporary copy, outside the repository, 0700, purged after 24 hours |
| composition root | `kit.py` | the only file naming concretes |
| harness | `adw.py` | ADW id, the answer JSON, a request log line |

## Rules, enforced in code

1. **Read-only.** `IPhoneReader` has three methods and none of them writes; `reader.ps1` holds no delete, move or
   rename verb (a test reads the script and asserts it). The only copy made is *from* the phone.
2. **Only the authorized phone.** A device is read only if its name is in `settings.json`'s `devices`. That file
   is edited on the machine by `adw_android_authorize.py`; no Bridge operation can add a device.
3. **Only the permitted folders** (`settings.json` `folders`, default `DCIM/Camera` and three common siblings).
4. **Only what was asked for.** A listing copies the N newest (at most 20) and publishes only their thumbnails;
   an original leaves the machine only for `get_photo`, one at a time, at most 7 MB.
5. **Never round a lock.** No device, a locked phone, charge-only mode and an unauthorized phone are each a
   state (`no_device`, `locked`, `not_authorized`) with the words for what EG must do. Nothing retries another way.
6. **A name is a name.** `--name` may not contain a path separator or start with `.` or `-`; checked at the
   gateway, at the worker and in the ADW.

## Order

Candidates are ranked without copying anything: the timestamp Android puts in the file name
(`IMG_20261114_093015.jpg`, `PXL_...`), else the modified date the phone reports. The N chosen are copied, and
then sorted again by EXIF `DateTimeOriginal` where the file has one. Each photo's `taken_source` says which of
the three its `taken_at` came from (`exif`, `filename`, `modified`).

## What is verified

| | state |
|---|---|
| ADW operations against a fake phone | 15 offline tests |
| ADW against a stand-in Windows folder through the real `reader.ps1` | run live on BATI |
| worker agent | 9 offline tests |
| gateway tools, image delivery, per-client isolation | 11 tests in `tests/android_tests.php` |
| a real phone over MTP | **not verified** |
| deployed gateway / ChatGPT showing a picture | **not verified** |

Open until a real phone is attached: whether the Windows shell shows an MTP device to a process started from the
worker's service, how long listing a large `DCIM/Camera` takes, and whether ChatGPT renders fifteen image blocks
from one answer (if not, `android_get_thumbnail` fetches them one at a time).

Tests: `cd adws && uv run --quiet --with pydantic --with pytest --with pillow pytest -q android_photos/tests`.
