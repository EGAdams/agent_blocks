"""Prepare explicitly requested local artifacts without general filesystem tools."""
import base64
import json
import os
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

WORKSPACE = Path(__file__).resolve().parents[3]


class ReportAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str
    mimeType: str = "text/html"
    content: str


class ContactAliases(BaseModel):
    model_config = ConfigDict(extra="forbid")
    aliases: dict[str, str] = Field(default_factory=dict)


def confirmed_contacts(request: str, path: Path | None = None) -> str:
    source = path or Path(os.environ.get("YOLANDA_CONTACTS_FILE",
                                        str(Path.home() / ".config/agent_blocks/yolanda_contacts.json")))
    if not source.exists():
        return "No contact aliases have been confirmed. Ask for an unknown recipient's actual address."
    contacts = ContactAliases.model_validate_json(source.read_text()).aliases
    selected = {}
    for alias, address in contacts.items():
        if re.search(rf"\b{re.escape(alias)}\b", request, re.I) and re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", address):
            selected[alias] = address
    return "User-confirmed recipient aliases: " + json.dumps(selected)


def latest_report_attachment(request: str, root: Path | None = None) -> ReportAttachment | None:
    if not (re.search(r"\battach\w*\b", request, re.I) and
            re.search(r"\bfinanc(?:e|ial)\s+report\b", request, re.I)):
        return None
    if not re.search(r"\b(latest|newest|fresh(?:est)?)\b", request, re.I) or \
       re.search(r"\b(don't|do not|never)\s+attach|\bwithout attaching\b", request, re.I):
        return None
    if set(re.findall(r"\b20\d{2}\b", request)) - {"2025"}:
        return None  # The installed report is specifically 2025; never substitute another year.
    folder = (root or WORKSPACE / "adws/adw_data/web_pages").resolve()
    manifest = folder / "index.json"
    if not manifest.is_file():
        return None
    for entry in json.loads(manifest.read_text()):
        name = entry.get("file", "")
        if not re.fullmatch(r"ROL_2025_Finance_Report_[A-Za-z0-9_]+\.html", name):
            continue
        page = (folder / name).resolve()
        if not page.is_relative_to(folder) or not page.is_file():
            continue
        if page.stat().st_size > 20 * 1024 * 1024:
            raise ValueError("Published report exceeds the supported attachment size")
        return ReportAttachment(filename=name, content=base64.b64encode(page.read_bytes()).decode("ascii"))
    return None


class EmailRequestContext:
    def __init__(self, request: str, report_root: Path | None = None, contacts_path: Path | None = None) -> None:
        self.report = latest_report_attachment(request, report_root)
        self.notes = confirmed_contacts(request, contacts_path)
        if self.report:
            self.notes += (f"\nThe published local 2025 finance report {self.report.filename} is ready. "
                           "It is outside the mailbox. Do not search Gmail for this file or ask the user to locate it. "
                           "The application attaches the exact file bytes before create_draft or update_draft, "
                           "or send_message when sending a new message. Do not encode or invent file content yourself.")

    def tool_input(self, name: str, data: dict, prefix: str) -> dict | None:
        operation = name.removeprefix(prefix)
        if not self.report or not name.startswith(prefix) or operation not in {"create_draft", "update_draft", "send_message"}:
            return None
        if operation == "send_message" and data.get("draftId"):
            return None  # Existing drafts must be updated through update_draft first.
        attachments = [a for a in (data.get("attachments") or []) if a.get("filename") != self.report.filename]
        return {**data, "attachments": [*attachments, self.report.model_dump()]}
