"""Which highlight on a page is blinking -- decided from what the page reports, never from a description.

The CDP probe (`cdp_browser/highlights.py`) returns, for each highlighted element, its classes, its
computed style, its running animations with their keyframes, and its background sampled every 100 ms.
This module turns that into one verdict per element:

- `blinking` -- the background alternates between a highlight colour and none while the text stays
  visible. Proven either by a running, endlessly repeating animation whose keyframes switch the
  background between a coloured and a transparent value (and leave colour, opacity and visibility
  alone), or by the samples themselves showing both a coloured and a transparent background;
- `highlighted` -- a steady coloured background (a `<mark>`, a saved `{{highlight}}`);
- `text_visible` -- false when the text itself is hidden at any point (opacity 0, visibility hidden,
  a transparent text colour): the dashboard's rule is that the background blinks, never the words.

Two independent proofs are kept apart in `evidence`, because a background tab throttles timers (the
samples may all land on one frame) while the keyframes still say what the page will draw.
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

_RGB = re.compile(r"rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:\s*[,/]\s*([\d.]+%?))?\s*\)")
_HEX = re.compile(r"#([0-9a-f]{3,8})\b", re.I)
_NAMED = {"transparent": (0, 0, 0, 0.0), "yellow": (255, 255, 0, 1.0), "white": (255, 255, 255, 1.0)}


def parse_color(value: str | None) -> tuple[float, float, float, float] | None:
    """(r, g, b, alpha) from a computed or keyframe colour; None when it names none we can read."""
    text = (value or "").strip().lower()
    if not text or text in ("none", "initial", "inherit", "unset"):
        return None
    if text in _NAMED:
        return _NAMED[text]
    match = _RGB.search(text)
    if match:
        r, g, b, a = match.groups()
        alpha = 1.0 if a is None else (float(a[:-1]) / 100 if a.endswith("%") else float(a))
        return float(r), float(g), float(b), alpha
    match = _HEX.search(text)
    if match:
        digits = match.group(1)
        if len(digits) in (3, 4):
            digits = "".join(c * 2 for c in digits)
        r, g, b = (int(digits[i:i + 2], 16) for i in (0, 2, 4))
        alpha = int(digits[6:8], 16) / 255 if len(digits) == 8 else 1.0
        return r, g, b, alpha
    return None


def is_clear(value: str | None) -> bool:
    color = parse_color(value)
    return color is not None and color[3] == 0


def is_highlight(value: str | None) -> bool:
    """A visible, light, warm background -- the yellows and ambers a highlighter uses."""
    color = parse_color(value)
    if color is None or color[3] <= 0.05:
        return False
    r, g, b, _ = color
    return r >= 200 and g >= 160 and b <= 210 and r - b >= 40


def color_name(value: str | None) -> str:
    if is_clear(value):
        return "none"
    if is_highlight(value):
        return "yellow" if parse_color(value)[1] >= 200 else "amber"
    return "other" if parse_color(value) else "unknown"


class HighlightState(BaseModel):
    text: str
    tag: str = ""
    classes: list[str] = Field(default_factory=list)
    css: str = ""
    blinking: bool = False
    highlighted: bool = False
    text_visible: bool = True
    background: str = ""
    colors_seen: list[str] = Field(default_factory=list)
    animation: str = ""
    evidence: list[str] = Field(default_factory=list)


def _text_hidden(color: str | None, opacity: str | None, visibility: str | None) -> bool:
    if visibility and visibility.strip() == "hidden":
        return True
    try:
        if opacity not in (None, "") and float(opacity) == 0:
            return True
    except ValueError:
        pass
    return is_clear(color)


def classify(row: dict[str, Any]) -> HighlightState:
    """One element's verdict from the probe's row for it."""
    style = row.get("style") or {}
    state = HighlightState(text=str(row.get("text") or ""), tag=str(row.get("tag") or ""),
                           classes=[str(c) for c in row.get("classes") or []], css=str(row.get("css") or ""),
                           background=str(style.get("background_color") or ""))
    hidden = _text_hidden(style.get("color"), style.get("opacity"), style.get("visibility"))
    for anim in row.get("animations") or []:
        frames = anim.get("keyframes") or []
        backgrounds = [f.get("background") for f in frames if f.get("background")]
        running = anim.get("play_state") == "running"
        endless = anim.get("iterations") == "infinite"
        switches = any(is_highlight(b) for b in backgrounds) and any(is_clear(b) for b in backgrounds)
        if any(_text_hidden(f.get("color"), f.get("opacity"), f.get("visibility")) for f in frames):
            hidden = True
        if running and endless and switches:
            state.blinking = True
            state.animation = str(anim.get("name") or "")
            state.evidence.append(f"animation {state.animation or '(unnamed)'} switches the background "
                                  f"between {' and '.join(sorted({color_name(b) for b in backgrounds}))}")
    samples = [str(s).split("|") for s in row.get("samples") or []]
    backgrounds = [s[0] for s in samples if s]
    if samples:
        seen = sorted({color_name(b) for b in backgrounds})
        state.colors_seen = seen
        if any(is_highlight(b) for b in backgrounds) and any(is_clear(b) for b in backgrounds):
            state.blinking = True
            state.evidence.append(f"sampled background changed {_changes(backgrounds)} times in "
                                  f"{len(samples) * 100} ms ({', '.join(seen)})")
        if any(len(s) >= 4 and _text_hidden(s[1], s[2], s[3]) for s in samples):
            hidden = True
    state.highlighted = state.blinking or is_highlight(state.background) or any(is_highlight(b) for b in backgrounds)
    if state.highlighted and not state.blinking:
        state.evidence.append(f"steady {color_name(state.background)} background")
    state.text_visible = not hidden
    return state


def _changes(values: list[str]) -> int:
    return sum(1 for a, b in zip(values, values[1:]) if a != b)


def classify_page(found: dict[str, Any]) -> list[HighlightState]:
    """Every highlighted element the probe found, nested duplicates of one highlight folded together.

    A blinking `<mark>` inside a highlighted paragraph is reported once, as the inner element, because
    that is the one whose text is the highlighted text."""
    states = [classify(r) for r in found.get("items") or []]
    return [s for s in states if s.highlighted or s.blinking]


def blinking(states: list[HighlightState]) -> list[HighlightState]:
    return [s for s in states if s.blinking]


def normalize(text: str) -> str:
    """Rendered text and source text compared fairly: Markdown emphasis marks and spacing dropped."""
    text = re.sub(r"\{\{/?highlight(?::[a-z-]+)?\}\}", "", text or "")
    text = re.sub(r"[*_`]", "", text)
    return " ".join(text.split()).strip().lower()
