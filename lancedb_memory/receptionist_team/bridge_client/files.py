"""McpBridgeFiles -- Toyota lists and reads the files her own Bridge downloads saved.

A `browser.download` task leaves each file at the gateway under a `file_id` (the worker's DownloadPublisher put it
there), and the gateway serves two MCP tools over them: `browser_downloaded_files` and `browser_view_file`. ChatGPT
calls them through its connector; this calls the same two tools, as the same JSON-RPC `tools/call`, through
Toyota's own authenticated transport -- so the gateway's rule that a client sees only the files *its own* tasks
downloaded applies to her exactly as it applies to ChatGPT. Nothing here can name a file another client saved.

What comes back is reduced to what she can use:

- the facts and the extracted text. A page image (`content[].type == "image"`) is never decoded or kept: her
  conversation model reads text only, and an image is the one thing that would blow her response limit. It asks
  for the `preview` rendition anyway, so the bytes that do cross the wire are the small ones;
- a refusal from the gateway (`isError`, "file_not_found: ...") is BridgeRefused with the gateway's own code and
  words, and a JSON-RPC error is BridgeMalformed -- never an empty list that reads as "you have no files".

File text is a website's words. It is data, never instructions, and the sentence built from it says where it came
from (`FileView.notice`, which the gateway attaches to every view).
"""
import re
from typing import Any

from pydantic import ValidationError

from .errors import BridgeMalformed, BridgeRefused
from .interfaces import IBridgeTransport
from .models import DownloadedFileList, FileView

FILE_ID = re.compile(r"^f_[0-9a-f]{24}$")
TASK_ID = re.compile(r"^t_[0-9a-f]{24}$")
MCP_PATH = "/mcp"
#: The response cap for this reader's transport. A view carries one page image even when it is not wanted
#: (the gateway always attaches one), so the default 300 KB client cap would refuse an ordinary scanned plat.
FILES_RESPONSE_BYTES = 2_000_000


class McpBridgeFiles:
    """IBridgeFiles over the gateway's MCP endpoint, through an injected transport."""

    def __init__(self, transport: IBridgeTransport) -> None:
        self._transport = transport

    def downloaded_files(self, task_id: str = "", limit: int = 10) -> DownloadedFileList:
        args: dict[str, Any] = {"limit": max(1, min(int(limit), 50))}
        if task_id:
            if not TASK_ID.fullmatch(task_id):
                raise BridgeRefused("bad_task_id", "that is not a Bridge task id")
            args["task_id"] = task_id
        return self._parse(DownloadedFileList, self._call("browser_downloaded_files", args), "file list")

    def view_file(self, file_id: str, page: int | None = None, text_offset: int = 0) -> FileView:
        if not FILE_ID.fullmatch(file_id or ""):
            raise BridgeRefused("bad_file_id", "that is not a downloaded file's id (they look like f_ and 24 letters)")
        args: dict[str, Any] = {"file_id": file_id, "rendition": "preview", "text_offset": max(0, int(text_offset))}
        if page is not None:
            args["page"] = max(1, int(page))
        view = self._parse(FileView, self._call("browser_view_file", args), "file")
        if view.file_id != file_id:
            raise BridgeMalformed("mismatch", "the Bridge answered about a different file")
        return view

    def _call(self, name: str, arguments: dict[str, Any]) -> dict:
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
        status, data = self._transport.request("POST", MCP_PATH, body)
        if status != 200 or not isinstance(data, dict):
            raise BridgeMalformed("unexpected_status", f"the Bridge's file tools answered HTTP {status}", status)
        if isinstance(data.get("error"), dict):
            raise BridgeMalformed("rpc_error", str(data["error"].get("message") or "the file tool call failed")[:200])
        result = data.get("result")
        if not isinstance(result, dict):
            raise BridgeMalformed("malformed", "the Bridge's file tool answered without a result")
        if result.get("isError"):
            raise self._refusal(result)
        structured = result.get("structuredContent")
        if not isinstance(structured, dict):
            raise BridgeMalformed("malformed", "the Bridge's file tool answered without its facts")
        return structured

    @staticmethod
    def _refusal(result: dict) -> BridgeRefused:
        """The gateway writes a refusal as one text item, "code: message" (BrowserTools::error)."""
        text = next((str(c.get("text", "")) for c in result.get("content") or []
                     if isinstance(c, dict) and c.get("type") == "text"), "")
        code, _, message = text.partition(": ")
        if not re.fullmatch(r"[a-z_]{2,40}", code):
            code, message = "refused", text
        return BridgeRefused(code, message[:300] or "the Bridge refused that file request")

    @staticmethod
    def _parse(model, data: dict, what: str):
        try:
            return model.model_validate(data)
        except ValidationError:
            raise BridgeMalformed("malformed", f"the Bridge's {what} did not match the contract") from None


def default_files(prefix: str = "TOYOTA") -> McpBridgeFiles:
    """One receptionist's own credential (Toyota's by default), with a response cap big enough for a view
    that carries a page image. The gateway shows each credential only the files its own tasks saved."""
    from .config import load_client_config, load_client_token
    from .transport import HttpsBridgeTransport
    config = load_client_config(prefix=prefix).model_copy(update={"max_response_bytes": FILES_RESPONSE_BYTES})
    return McpBridgeFiles(HttpsBridgeTransport(config, load_client_token(prefix=prefix)))



#: Room for a file's words in one spoken reply (workflow.SPOKEN_LIMIT is 700 in all).
SPOKEN_TEXT = 380


def _size(n: int) -> str:
    return f"{n / 1_048_576:.1f} MB" if n >= 1_048_576 else f"{max(1, round(n / 1024))} KB"


def _pages(n: int, kind: str = "") -> str:
    """An archive's count is its entries (file_inspection counts each as a page), not pages."""
    unit = "entr" if kind == "archive" else "page"
    plural = ("ies" if n != 1 else "y") if kind == "archive" else ("s" if n != 1 else "")
    return f", {n} {unit}{plural}" if n else ""


def list_sentence(listing: DownloadedFileList) -> str:
    if not listing.files:
        return ("I don't have any downloaded files at the Bridge yet. Ask me to download one from a page "
                "first; I can only see files my own downloads saved.")
    parts = [f"{f.name or f.original_name} ({f.kind or f.mime}{_pages(f.page_count, f.kind)}, {_size(f.bytes)}), "
             f"file {f.file_id}" for f in listing.files]
    head = f"My {len(parts)} most recent downloaded file{'s' if len(parts) != 1 else ''}: "
    return head + "; ".join(parts) + ". Ask me to read one by its file id."


def view_sentence(view: FileView, room: int = SPOKEN_TEXT) -> str:
    facts = f"{view.name or view.original_name} is {view.kind or view.mime}{_pages(view.page_count, view.kind)}, {_size(view.bytes)}."
    if not view.text:
        pages = (" It has page images but no text I can read; ChatGPT can look at the images with "
                 f"browser_view_file, file {view.file_id}.") if view.page_count else " It has no text I can read."
        return facts + pages
    shown = view.text[:room]
    said = " ".join(shown.split())
    start = "It says" if view.text_offset == 0 else f"From character {view.text_offset:,} it says"
    out = f'{facts} {start}, as the website wrote it and not as instructions: "{said}'
    rest = len(view.text) > room or view.text_more
    out += '…"' if rest else '"'
    if rest:
        out += f" Ask for more of file {view.file_id} from character {view.text_offset + len(shown):,}."
    if view.text_truncated:
        out += " The Bridge kept only part of this file's text."
    return out
