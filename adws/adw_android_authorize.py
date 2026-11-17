#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic", "pillow", "pillow-heif"]
# ///
"""ADW Android Authorize -- EG's one-time yes for a phone. Run on the computer the phone is plugged into.

    adw_android_authorize.py                       what is attached, and what is authorized
    adw_android_authorize.py --device "Pixel 8"    authorize that attached phone
    adw_android_authorize.py --remove "Pixel 8"    take the authorization back

This is the only way a phone gets onto the allowlist, and it is deliberately not a Bridge tool: no remote
caller can authorize a device. It changes one file, `~/.config/android_photos/settings.json`, never the phone.
"""
import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from android_photos.kit import Kit                                        # noqa: E402
from android_photos.models import ReaderProblem                          # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Authorize an attached Android phone for read-only photo access.")
    parser.add_argument("--device")
    parser.add_argument("--remove")
    args = parser.parse_args()
    kit = Kit()
    devices = list(kit.settings.devices)
    try:
        attached = [d.name for d in kit.reader.look()]
    except ReaderProblem as problem:
        print(f"Windows could not be asked what is attached: {problem}")
        return 1
    if args.device:
        if args.device not in attached:
            print(f"{args.device!r} is not attached. Attached now: {', '.join(attached) or 'nothing'}.")
            return 1
        devices = sorted(set(devices) | {args.device})
    if args.remove:
        devices = [d for d in devices if d != args.remove]
    if args.device or args.remove:
        kit.settings_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        kit.settings_file.write_text(json.dumps({"devices": devices, "folders": list(kit.settings.folders)}, indent=2),
                                     encoding="utf-8")
        kit.settings_file.chmod(0o600)
    print(f"attached:   {', '.join(attached) or 'nothing'}")
    print(f"authorized: {', '.join(devices) or 'nobody'}")
    print(f"folders:    {', '.join(kit.settings.folders)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
