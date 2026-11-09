"""HIGHLIGHTS_JS: which text on a page is highlighted, and which highlight is blinking, read out of the DOM.

`body_text` answers "what does the page say"; it cannot say that one line of it has a yellow
background, let alone that the background is switching on and off. This script answers that from the
page itself, in three independent ways, so a caller never needs a person to describe what they see:

- **classes** -- every `<mark>` and every element whose class names a highlight, an active line or a
  blink, inside the root;
- **computed style and running animations** -- `getComputedStyle` (background, colour, opacity,
  visibility) and `element.getAnimations()`, with each animation's name, play state, duration,
  iteration count and the background/colour/opacity/visibility of its keyframes;
- **samples** -- the computed background read every `STEP` ms for `SAMPLE_MS` ms (even in a hidden tab,
  whose timers Chrome slows), which is what a
  person's eye sees whatever the stylesheet says. Sampling is skipped when nothing was found.

It is async (it waits between samples), so it must be evaluated with `awaitPromise`. It reads text and
styles only: no field's typed value is ever read.
"""
from __future__ import annotations

HIGHLIGHTS_JS = r"""
(async function (rootCss, limit, sampleMs) {
  const root = rootCss ? document.querySelector(rootCss) : document.body;
  if (!root) return { root_found: false, root: rootCss, items: [] };
  const clean = (s) => (s || '').trim().replace(/\s+/g, ' ');
  const cssFor = (el) => {
    if (el.id && !/[:.\s]/.test(el.id)) return '#' + CSS.escape(el.id);
    const parts = [];
    for (let n = el; n && n.nodeType === 1 && n !== document.body; n = n.parentElement) {
      if (n.id && !/[:.\s]/.test(n.id)) { parts.unshift('#' + CSS.escape(n.id)); break; }
      let i = 1;
      for (let s = n.previousElementSibling; s; s = s.previousElementSibling) if (s.tagName === n.tagName) i++;
      parts.unshift(n.tagName.toLowerCase() + ':nth-of-type(' + i + ')');
    }
    return parts.join(' > ');
  };
  const seen = new Set();
  const found = [];
  const add = (el) => { if (el && el.nodeType === 1 && root.contains(el) && !seen.has(el)) { seen.add(el); found.push(el); } };
  root.querySelectorAll('mark, [class*=highlight], [class*=active-line], [class*=blink]').forEach(add);
  if (document.getAnimations) {
    for (const a of document.getAnimations()) { const t = a.effect && a.effect.target; if (t) add(t); }
  }
  const items = found.slice(0, limit);
  const frames = (a) => {
    try {
      return (a.effect.getKeyframes() || []).map((k) => ({
        offset: k.computedOffset, background: k.backgroundColor || k.background || null,
        color: k.color || null, opacity: k.opacity === undefined ? null : String(k.opacity),
        visibility: k.visibility || null }));
    } catch (e) { return []; }
  };
  const rows = items.map((el) => {
    const cs = getComputedStyle(el);
    const anims = (el.getAnimations ? el.getAnimations() : []).map((a) => {
      const t = a.effect && a.effect.getComputedTiming ? a.effect.getComputedTiming() : {};
      return { name: a.animationName || a.id || '', play_state: a.playState,
               duration_ms: typeof t.duration === 'number' ? t.duration : null,
               iterations: t.iterations === Infinity ? 'infinite' : (t.iterations === undefined ? null : t.iterations),
               keyframes: frames(a) };
    });
    const r = el.getBoundingClientRect();
    return { text: clean(el.textContent).slice(0, 2000), tag: el.tagName.toLowerCase(),
             classes: [...el.classList], css: cssFor(el),
             style: { background_color: cs.backgroundColor, color: cs.color, opacity: cs.opacity,
                      visibility: cs.visibility, display: cs.display },
             on_screen: r.width > 0 && r.height > 0, animations: anims, samples: [] };
  });
  if (rows.length && sampleMs > 0) {
    const STEP = 100;
    // A hidden tab (a minimised or covered window) gets its timers slowed to one a second or less, so
    // a setTimeout wait there overran the caller's deadline. A MessageChannel round trip is not slowed;
    // the loop is also bounded by elapsed time, so a slow wait means fewer samples, never a late answer.
    const nap = (ms) => document.hidden
      ? new Promise((ok) => {
          const end = performance.now() + ms, ch = new MessageChannel();
          ch.port1.onmessage = () => (performance.now() >= end ? (ch.port1.close(), ok()) : ch.port2.postMessage(0));
          ch.port2.postMessage(0);
        })
      : new Promise((ok) => setTimeout(ok, ms));
    const start = performance.now();
    while (performance.now() - start < sampleMs) {
      items.forEach((el, i) => {
        const cs = getComputedStyle(el);
        rows[i].samples.push([cs.backgroundColor, cs.color, cs.opacity, cs.visibility].join('|'));
      });
      await nap(STEP);
    }
  }
  return { root_found: true, root: rootCss, total: found.length, hidden_page: document.hidden, items: rows };
})(ROOT, LIMIT, SAMPLE_MS)
"""

# The sha256 and length of a field's value, computed in the page, so a caller can compare it with a
# saved revision without the text ever leaving the page.
TEXT_DIGEST_JS = r"""
(async function (css) {
  const el = document.querySelector(css);
  if (!el) return { found: false };
  const text = 'value' in el ? el.value : el.textContent;
  if (!(window.crypto && crypto.subtle)) return { found: true, chars: text.length, sha256: null };
  const d = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return { found: true, chars: text.length,
           sha256: [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, '0')).join('') };
})(CSS_SELECTOR)
"""
