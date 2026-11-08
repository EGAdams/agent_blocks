"""Does pressing this element save a file rather than go somewhere?

One responsibility, in its own module because both operations need it and neither can import the
other: `browser.click` asks in order to refuse and name `browser.download`, and the ADW's own
documentation quotes the same list. Before this, a click on Michigan's `0337201.tif` anchor
answered "Clicked '0337201.tif'." and saved nothing -- a true sentence about a failed job, and the
hardest kind of failure to chase, because the only honest report said success.

Two signals, both read off the element and both checkable -- never a guess from the page's shape:

- an `<a download>` attribute, which is the author saying so outright;
- a document extension at the end of the href's path, or of the element's own text. The text is
  what catches an ASP.NET postback link, whose href is `javascript:__doPostBack(...)` and names no
  file at all while the row plainly reads `0337201.tif`.

The list is deliberately short and holds only extensions a browser saves rather than renders.
`.html`, `.php`, `.aspx` and `.jsp` are absent because those are pages; `.txt`, `.json`, `.xml`,
`.png`, `.jpg` and `.svg` are absent because Chrome shows them in a tab. A wrong answer here costs
a working click, so the test for adding an extension is "Chrome offers to save it", not "it looks
like a file".
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

#: Extensions Chrome saves instead of rendering. PDF is the one judgement call: Chrome renders it in
#: the built-in viewer by default, but the same anchor is what a person means by "download the
#: statement", and browser.download keeps it either way -- so it belongs here.
SAVED_EXTENSIONS = frozenset({
    "tif", "tiff", "pdf", "csv", "xls", "xlsx", "xlsm", "doc", "docx", "ppt", "pptx",
    "zip", "7z", "rar", "gz", "tgz", "ofx", "qfx", "qbo", "dat", "exe", "msi", "dmg",
})
#: A trailing .ext on a path or a file name, with nothing but the extension after the dot.
_EXTENSION = re.compile(r"\.([A-Za-z0-9]{1,5})$")


def _extension(text: str) -> str:
    found = _EXTENSION.search((text or "").strip().rstrip("/"))
    return found.group(1).lower() if found else ""


def saved_extension(element) -> str:
    """The extension this element would save, or "" if pressing it does not save a file.

    Never raises: an element that cannot be asked for an attribute is simply not a file link.
    """
    try:
        if element.get_attribute("download") is not None and (element.tag_name or "").lower() == "a":
            return _extension(str(element.get_attribute("download") or "")) or "file"
        href = str(element.get_attribute("href") or "")
        text = str(element.text or "")
    except Exception:
        return ""
    for candidate in (_extension(urlparse(href).path), _extension(text)):
        if candidate in SAVED_EXTENSIONS:
            return candidate
    return ""
