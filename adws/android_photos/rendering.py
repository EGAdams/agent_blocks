"""IPhotoRenderer over Pillow: a JPEG that any viewer can show, upright, at one of two sizes.

    preview   a thumbnail, small enough that fifteen fit in one answer
    full      the photograph at viewing size

HEIC needs `pillow-heif`; it is registered if it is installed, and a HEIC file without it fails with words
that say so. The original file is never changed: it is opened, and a new file is written beside the cache.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from .models import Rendition

LONG_SIDE = {"preview": 512, "full": 2048}
QUALITY = {"preview": 78, "full": 86}


class RenderFailed(Exception):
    pass


class PillowRenderer:
    def render(self, original: Path, rendition: str, out_dir: Path) -> Rendition:
        try:
            from PIL import Image, ImageOps
            try:
                import pillow_heif
                pillow_heif.register_heif_opener()
            except ImportError:
                pass
            with Image.open(original) as image:
                image = ImageOps.exif_transpose(image)               # upright, as the phone showed it
                image.thumbnail((LONG_SIDE[rendition],) * 2)
                image = image.convert("RGB")
                out_dir.mkdir(parents=True, exist_ok=True)
                out = out_dir / f"{original.stem}.{rendition}.jpg"
                image.save(out, "JPEG", quality=QUALITY[rendition], optimize=True)
                width, height = image.size
        except Exception as exc:                                     # noqa: BLE001 - Pillow raises many kinds
            hint = " (HEIC needs pillow-heif)" if original.suffix.lower() in (".heic", ".heif") else ""
            raise RenderFailed(f"{original.name} could not be turned into a picture{hint}: {type(exc).__name__}") from None
        data = out.read_bytes()
        return Rendition(rendition=rendition, path=out, width=width, height=height, bytes=len(data),
                         sha256=hashlib.sha256(data).hexdigest())
