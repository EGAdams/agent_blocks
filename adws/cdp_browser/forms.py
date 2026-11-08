"""FORM_CONTROLS_JS: what a page offers a person to operate, read out of the DOM.

`INTERACTIVE_JS` (browser_workflows) already lists clickable things for diagnostics.
This script answers the other question -- "what can I type in or choose from here, what
is it called, and what would I pass as a selector?" -- so a caller never has to guess a
CSS selector. For every control it reports the stable ways of naming it (its id, its
`name`, its `<label>`, its aria-label), whether it can be operated now, and for a
dropdown every option with the one currently chosen.

What it will not report: a hidden input (that is where a site keeps its tokens) is left
out entirely, and no field's typed text is ever read -- a password field comes back as
`secret`, and every other field only says *whether* it holds something, never what.
"""
from __future__ import annotations

FORM_CONTROLS_JS = r"""
(function (limit) {
  const SELECTOR = 'input:not([type=hidden]), select, textarea, button, a[id], ' +
                   '[role=button], [role=combobox], [role=textbox], [contenteditable=""], [contenteditable=true]';
  const SECRET = /pass(word|wd|code)?|^pwd$|\bpin\b|otp|one.?time|mfa|2fa|totp|secret|token|apikey|api.?key|cvv|cvc|ssn/i;

  const clean = (s) => (s || '').trim().replace(/\s+/g, ' ');

  // The stable ways of naming one node, best first: its own id, then an attribute that
  // happens to be unique on this page, and only then a positional path.
  const cssFor = (el) => {
    if (el.id && !/\d{4,}|[:.\s]/.test(el.id)) return '#' + CSS.escape(el.id);
    for (const a of ['data-testid', 'name', 'aria-label', 'placeholder', 'title']) {
      const v = el.getAttribute(a);
      if (v) {
        const s = el.tagName.toLowerCase() + '[' + a + '="' + v.replace(/(["\\])/g, '\\$1') + '"]';
        try { if (document.querySelectorAll(s).length === 1) return s; } catch (e) { /* not a selector */ }
      }
    }
    const parts = [];
    for (let n = el; n && n.nodeType === 1 && n !== document.body; n = n.parentElement) {
      let i = 1;
      for (let s = n.previousElementSibling; s; s = s.previousElementSibling) if (s.tagName === n.tagName) i++;
      parts.unshift(n.tagName.toLowerCase() + ':nth-of-type(' + i + ')');
    }
    return 'body > ' + parts.join(' > ');
  };

  // What a person reads beside the control, best first. Accela (and plenty of ASP.NET forms)
  // set aria-label to the control's own id, which names nothing -- a candidate that is just the
  // id or the name is dropped and the search goes on. A form's punctuation ("Record Type:") is
  // kept, because it is what is on the screen; the operations forgive it when matching.
  const labelFor = (el) => {
    const useless = [clean(el.id).toLowerCase(), clean(el.getAttribute('name')).toLowerCase()].filter(Boolean);
    const byId = () => {
      if (!el.id) return '';
      try { const t = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
            return t ? clean(t.innerText || t.textContent) : ''; } catch (e) { return ''; }
    };
    const byRef = () => {
      const by = el.getAttribute('aria-labelledby');
      if (!by) return '';
      return by.split(/\s+/).map(function (id) {
        const n = document.getElementById(id);
        return n ? clean(n.innerText || n.textContent) : '';
      }).filter(Boolean).join(' ');
    };
    const wrapping = () => {
      const tag = el.closest ? el.closest('label') : null;
      return tag ? clean(tag.innerText || tag.textContent) : '';
    };
    for (const pick of [byId, () => clean(el.getAttribute('aria-label')), byRef, wrapping,
                        () => clean(el.getAttribute('placeholder')), () => clean(el.getAttribute('title'))]) {
      const found = pick();
      // A title like "Visit javascript:WebForm_DoPostBack(...)", or a bare "Visit #", names nothing a
      // person would say, so the search goes on to the next candidate instead of reporting it.
      if (found && useless.indexOf(found.toLowerCase()) < 0 && !/javascript:|^https?:/i.test(found)
          && !/^visit\s*#?$/i.test(found)) return found.slice(0, 120);
    }
    return '';
  };

  const roleOf = (el, type) => {
    const tag = el.tagName.toLowerCase();
    if (tag === 'select' || el.getAttribute('role') === 'combobox') return 'dropdown';
    if (tag === 'textarea' || el.isContentEditable || el.getAttribute('role') === 'textbox') return 'editable';
    if (tag === 'button' || el.getAttribute('role') === 'button') return 'button';
    if (type === 'password') return 'password';
    if (type === 'checkbox' || type === 'radio') return type;
    if (type === 'submit' || type === 'button' || type === 'reset' || type === 'image') return 'button';
    return 'text';
  };

  const rows = [];
  for (const el of document.querySelectorAll(SELECTOR)) {
    const tag  = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    let   role = roleOf(el, type);
    if (tag === 'a') role = 'button';
    // An anchor counts only when it acts as a control: Accela's "Search" is an <a> whose href
    // posts the form. A page's ordinary navigation links are browser.read's business, not a form's.
    const acts = el.getAttribute('role') === 'button' || !!el.getAttribute('onclick')
                 || /^javascript:/i.test(el.getAttribute('href') || '');
    if (tag === 'a' && !acts) continue;
    const r     = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    const label = labelFor(el);
    const name  = el.getAttribute('name') || '';
    const words = (el.id + ' ' + name + ' ' + label + ' ' + (el.getAttribute('autocomplete') || '')).replace(/[_\-$]/g, ' ');
    const secret = role === 'password' || SECRET.test(words);
    const row = {
      role:     secret && role !== 'button' ? 'password' : role,
      tag:      tag,
      type:     type,
      name:     name.slice(0, 160),
      id:       (el.id || '').slice(0, 160),
      label:    label || (role === 'button' ? clean(tag === 'input' ? el.value : el.innerText).slice(0, 120) : ''),
      css:      cssFor(el).slice(0, 300),
      // A button's caption is on the screen for everyone; a field's typed text never is.
      text:     role === 'button' ? clean(tag === 'input' ? el.value : (el.innerText || el.value)).slice(0, 120) : '',
      visible:  !!(r.width && r.height) && style.visibility !== 'hidden' && style.display !== 'none',
      enabled:  !el.disabled,
      readonly: !!el.readOnly || el.getAttribute('aria-readonly') === 'true',
      required: !!el.required || el.getAttribute('aria-required') === 'true',
      secret:   secret && role !== 'button',
      filled:   false,
      options:  [],
      selected: '',
      selected_value: '',
      // A list the site draws itself holds no <option> at all: its items are put into the page only
      // once it is open, so they cannot be listed from here and select_option has to open it first.
      site_drawn: false
    };
    if (row.role === 'dropdown' && el.options) {
      row.options = Array.from(el.options).slice(0, 200).map(function (o, i) {
        return { position: i + 1, value: o.value, text: clean(o.text).slice(0, 160),
                 selected: !!o.selected, disabled: !!o.disabled };
      });
      const chosen = row.options.find(function (o) { return o.selected; });
      if (chosen) { row.selected = chosen.text; row.selected_value = chosen.value; }
    } else if (row.role === 'dropdown') {
      row.site_drawn = true;
      row.selected = clean(el.value || el.innerText || el.textContent).slice(0, 160);
      row.selected_value = clean(el.getAttribute('data-value') || el.value).slice(0, 160);
    } else if (!row.secret && role !== 'button') {
      // Whether there is something in the field, never what it is.
      row.filled = !!clean(el.isContentEditable ? (el.textContent || '') : (el.value || ''));
    }
    rows.push(row);
  }
  // Visible first, so a cap never buries the controls a person can actually see.
  const ordered = rows.filter(function (x) { return x.visible; }).concat(rows.filter(function (x) { return !x.visible; }));
  return { total: rows.length, controls: ordered.slice(0, limit) };
})(LIMIT)
"""
