"""Find roughly where on a scanned tax-document page an open question's
answer sits, and show the page to the human with that region boxed in red.

Two tiers, cheapest first:

* `keyword_match_box` -- deterministic. Pull the amounts/reference numbers out
  of the question text, OCR the page, score every OCR line against those
  terms, and box the best match. No model call. This is the same Pillow-box
  pattern the `rol_finances` dashboard's own `document_annotation.py` already
  draws for receipts, but keyed off a free-text question instead of a stored
  expense's amount/date/merchant -- there is no expense row to match against
  here, so the search terms come out of the question itself.
* When that comes up empty, the caller (`adw_tax_intake.py`) falls back to
  the `localizer` agent -- vision via its own `read` tool, the same pattern
  the `locator` agent already uses on receipts -- and `annotate()` just draws
  whatever box it reports.

Opening the result is via `explorer.exe` (WSL -> Windows): this ADW runs
under WSL and the human is looking at a real Windows desktop, so the chosen
page is their own file browser, not a second viewer app.

Never raises: a missing tesseract, a missing explorer.exe, an unreadable
image -- all become a message, same as every other ADW adapter in this
package.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

PDF_SUFFIXES = {".pdf"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}

#: Loose enough to catch "$4,584.18", "4584.18", "check #5128", "5128" --
#: English words in a question are not specific enough for OCR text to match
#: on, but a dollar figure or a reference number usually appears verbatim.
AMOUNT_RE = re.compile(r"\$?\d[\d,]*\.\d{2}")
NUMBER_RE = re.compile(r"#?\d{3,}")


@dataclass
class BoxResult:
    found: bool
    x0: float = 0.0   # fraction of width/height, top-left origin
    y0: float = 0.0
    x1: float = 0.0
    y1: float = 0.0
    note: str = ""


def extract_terms(question: str) -> list[str]:
    """The concrete, searchable tokens in an open question -- longest first,
    so a full amount outscores a shorter substring of it."""
    terms = AMOUNT_RE.findall(question) + NUMBER_RE.findall(question)
    return sorted({t.lstrip("#") for t in terms}, key=len, reverse=True)


def _page_image(path: Path):
    """One rendered page as a Pillow image, PDF or plain image alike. None
    when the file is neither (or does not exist) -- never raises."""
    try:
        from PIL import Image
        if path.suffix.lower() in PDF_SUFFIXES:
            import fitz
            doc = fitz.open(str(path))
            if doc.page_count == 0:
                return None
            pix = doc[0].get_pixmap(dpi=200)
            return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        if path.suffix.lower() in IMAGE_SUFFIXES:
            return Image.open(path).convert("RGB")
        return None
    except Exception:
        return None


def keyword_match_box(image_path: Path, question: str) -> BoxResult:
    """OCR the page and box the line whose text best matches the question's
    searchable terms. No match worth drawing -> found=False, never a guess."""
    terms = extract_terms(question)
    if not terms:
        return BoxResult(found=False, note="no dollar amount or reference number in the "
                         "question to search for")

    image = _page_image(image_path)
    if image is None:
        return BoxResult(found=False, note=f"cannot render {image_path.suffix} for OCR")

    try:
        import pytesseract
        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
    except Exception as error:
        return BoxResult(found=False, note=f"OCR unavailable: {error}")

    width, height = image.size
    lines: dict[tuple, list[int]] = {}
    for i, text in enumerate(data["text"]):
        if not text.strip():
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append(i)

    best_score, best_idxs = 0, None
    for idxs in lines.values():
        line_text = " ".join(data["text"][i] for i in idxs).replace(",", "")
        score = sum(1 for term in terms if term.replace(",", "") in line_text)
        if score > best_score:
            best_score, best_idxs = score, idxs

    if not best_idxs:
        return BoxResult(found=False, note="no OCR line matched any term from the question")

    xs = [data["left"][i] for i in best_idxs] + [data["left"][i] + data["width"][i] for i in best_idxs]
    ys = [data["top"][i] for i in best_idxs] + [data["top"][i] + data["height"][i] for i in best_idxs]
    pad_x, pad_y = width * 0.01, height * 0.01
    x0, x1 = max(min(xs) - pad_x, 0), min(max(xs) + pad_x, width)
    y0, y1 = max(min(ys) - pad_y, 0), min(max(ys) + pad_y, height)
    return BoxResult(found=True, x0=x0 / width, y0=y0 / height, x1=x1 / width, y1=y1 / height,
                     note=f"matched {best_score} term(s) on one OCR line")


def annotate(image_path: Path, box: BoxResult, out_dir: Path) -> Path | None:
    """Draw a red rectangle over `box` on a temp copy of the page. Never
    touches the original -- the same guarantee the dashboard's own annotator
    keeps. None on any failure, including a box that was never found."""
    if not box.found:
        return None
    try:
        from PIL import ImageDraw
        image = _page_image(image_path)
        if image is None:
            return None
        width, height = image.size
        draw = ImageDraw.Draw(image)
        rect = (box.x0 * width, box.y0 * height, box.x1 * width, box.y1 * height)
        line_width = max(3, int(min(width, height) * 0.004))
        draw.rectangle(rect, outline="red", width=line_width)
        out_path = out_dir / f"{image_path.stem}_boxed.png"
        image.save(out_path)
        return out_path
    except Exception:
        return None


def open_in_windows(path: Path) -> str:
    """`explorer.exe <path>` from WSL, converting the path first. Never
    raises -- a non-WSL host, a missing explorer.exe/wslpath, or a bad path
    all become a message instead of a crash. `explorer.exe` itself routinely
    exits non-zero even on success, so this fires it and forgets rather than
    checking its return code."""
    try:
        converted = subprocess.run(["wslpath", "-w", str(path)], capture_output=True,
                                   text=True, timeout=5)
        if converted.returncode != 0:
            return f"Could not convert {path} to a Windows path: {converted.stderr.strip()}"
        subprocess.Popen(["explorer.exe", converted.stdout.strip()])
        return f"Opened {path.name} in Windows Explorer."
    except FileNotFoundError:
        return f"explorer.exe/wslpath not found -- open this file yourself: {path}"
    except OSError as error:
        return f"Could not open {path} in Explorer ({error}) -- open it yourself."


__all__ = ["BoxResult", "annotate", "extract_terms", "keyword_match_box", "open_in_windows"]
