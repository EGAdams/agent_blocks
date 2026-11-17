"""IPhoneReader over Windows PowerShell and `reader.ps1`, reached from WSL.

The phone is attached to Windows, not to WSL, so every look at it is one run of `reader.ps1` with a request
file and an answer file in a staging folder under the Windows user's own LocalAppData. A copied photograph
lands in that staging folder, is moved into the Linux-side cache, and the staging folder is removed.

Nothing a caller sends reaches the command line: the request travels as a JSON file, and the three operations
are named here, not by the caller. `root` exists for the tests (an ordinary folder standing in for the phone).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from collections.abc import Callable
from pathlib import Path, PureWindowsPath
from typing import Protocol

from .models import DeviceSeen, Listing, PHOTO_SUFFIXES, ReaderProblem, RemotePhoto

POWERSHELL = Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
SCRIPT = Path(__file__).resolve().parent / "reader.ps1"


class IWindowsLink(Protocol):
    def ensure(self) -> tuple[bool, str]: ...


def wsl_path(windows: str) -> Path:
    """C:\\Users\\x -> /mnt/c/Users/x."""
    pure = PureWindowsPath(windows)
    return Path("/mnt") / pure.drive.rstrip(":").lower() / Path(*pure.parts[1:])


class PowerShellPhoneReader:
    def __init__(self, link: IWindowsLink, root: str | None = None, powershell: Path = POWERSHELL,
                 run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> None:
        self._link, self._root, self._powershell, self._run = link, root, powershell, run
        self._staging: PureWindowsPath | None = None

    # -- IPhoneReader ----------------------------------------------------------------------------------------
    def look(self) -> list[DeviceSeen]:
        answer = self._ask({"op": "look"}, 60)
        return [DeviceSeen(name=str(d.get("name", "")), storages=[str(s) for s in d.get("storages") or []])
                for d in answer.get("devices") or []]

    def list(self, device: str, folders: list[str]) -> Listing:
        answer = self._ask({"op": "list", "device": device, "folders": folders}, 600)
        photos = [RemotePhoto(storage=str(f["storage"]), folder=str(f["folder"]), name=str(f["name"]),
                              modified=str(f.get("modified") or ""))
                  for f in answer.get("files") or [] if Path(str(f.get("name", ""))).suffix.lower() in PHOTO_SUFFIXES]
        found = sorted({f"{f['storage']}/{f['folder']}" for f in answer.get("folders") or []})
        return Listing(folders=found, photos=photos)

    def copy(self, device: str, photos: list[RemotePhoto], into: dict[str, Path]) -> dict[str, Path]:
        if not photos:
            return {}
        staging = self._staging_folder()
        drop = staging / f"copy-{uuid.uuid4().hex[:12]}"
        keys = {str(n): photo for n, photo in enumerate(photos)}
        items = [{"key": k, "storage": p.storage, "folder": p.folder, "name": p.name} for k, p in keys.items()]
        out: dict[str, Path] = {}
        try:
            wsl_path(str(drop)).mkdir(parents=True, exist_ok=True)
            answer = self._ask({"op": "copy", "device": device, "items": items, "dest": str(drop)}, 120 + 100 * len(items))
            for row in answer.get("copied") or []:
                photo = keys.get(str(row.get("key")))
                if photo is None or not row.get("ok"):
                    continue
                target = into[photo.key] / photo.name
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                shutil.copyfile(wsl_path(str(row["path"])), target)
                out[photo.key] = target
        except OSError as exc:
            raise ReaderProblem("reader_failed", f"a copied photo could not be collected: {type(exc).__name__}") from None
        finally:
            shutil.rmtree(wsl_path(str(drop)), ignore_errors=True)      # the staging copy on Windows, never the phone
        return out

    # -- one run of reader.ps1 -------------------------------------------------------------------------------
    def _ask(self, request: dict, seconds: int) -> dict:
        staging = self._staging_folder()
        folder = wsl_path(str(staging))
        token = uuid.uuid4().hex[:12]
        request_file, answer_file, script = f"request-{token}.json", f"answer-{token}.json", "reader.ps1"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(SCRIPT, folder / script)
            body = {**request, **({"root": self._root} if self._root else {})}
            (folder / request_file).write_text(json.dumps(body), encoding="utf-8")
            self._powershell_run(["-ExecutionPolicy", "Bypass", "-File", str(staging / script),
                                  str(staging / request_file), str(staging / answer_file)], seconds)
            answer = json.loads((folder / answer_file).read_text(encoding="utf-8-sig"))
        except ReaderProblem:
            raise
        except (OSError, ValueError) as exc:
            raise ReaderProblem("reader_failed", f"Windows gave no answer about the phone ({type(exc).__name__})") from None
        finally:
            for name in (request_file, answer_file):
                (folder / name).unlink(missing_ok=True)
        if not isinstance(answer, dict) or not answer.get("ok"):
            cause = str((answer or {}).get("cause") or "reader_failed") if isinstance(answer, dict) else "reader_failed"
            raise ReaderProblem(cause, str((answer or {}).get("error") or "the phone could not be read")[:300])
        return answer

    def _staging_folder(self) -> PureWindowsPath:
        if self._staging is None:
            done = self._powershell_run(["-Command", "[Environment]::GetFolderPath('LocalApplicationData')"], 60)
            base = (done.stdout or "").strip().splitlines()[-1:] or [""]
            if not PureWindowsPath(base[0]).drive:
                raise ReaderProblem("no_windows", "Windows did not say where its staging folder is")
            self._staging = PureWindowsPath(base[0]) / "android-photos" / "staging"
        return self._staging

    def _powershell_run(self, arguments: list[str], seconds: int) -> subprocess.CompletedProcess:
        ok, why = self._link.ensure()
        if not ok:
            raise ReaderProblem("no_windows", f"this machine cannot reach Windows from here ({why})")
        try:
            return self._run([str(self._powershell), "-NoProfile", "-NonInteractive", *arguments], capture_output=True,
                             text=True, timeout=seconds, stdin=subprocess.DEVNULL, cwd="/mnt/c")
        except subprocess.TimeoutExpired:
            raise ReaderProblem("reader_failed", f"Windows did not answer about the phone in {seconds} seconds") from None
        except OSError as exc:
            raise ReaderProblem("no_windows", f"PowerShell could not be started ({type(exc).__name__})") from None
