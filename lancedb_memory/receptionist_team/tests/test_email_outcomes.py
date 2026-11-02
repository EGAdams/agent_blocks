"""The failed draft asked useful questions; do not replace them with a connection error."""
import base64
import json
import tempfile
from pathlib import Path

from ..email.outcome import EmailTaskOutcome, outcome_text, tool_action
from ..email.request_context import EmailRequestContext, latest_report_attachment
from ..email.config import EmailConnectorSettings
from ..adapters.email_connector_runner import EmailConnectorRunner
from ..profiles import YOLANDA

PREFIX = "mcp__claude_ai_Gmail__"


async def test_clarification_is_preserved_without_a_final_question_mark():
    message = "Please provide Mom's actual email address. The report is ready; I won't guess a recipient."
    outcome = EmailTaskOutcome(status="needs_information", message=message)
    assert outcome_text(outcome, {"read"}) == message
    assert outcome_text(outcome, set()) == message


async def test_search_results_never_count_as_a_created_draft_or_sent_message():
    for action in ["draft", "send"]:
        outcome = EmailTaskOutcome(status="completed", message="Done.", action=action)
        assert outcome_text(outcome, {"read"}).startswith("No email action was confirmed")
        assert outcome_text(outcome, {action}) == "Done."
    assert tool_action(PREFIX + "search_threads", PREFIX) == "read"
    assert tool_action(PREFIX + "create_draft", PREFIX) == "draft"
    assert tool_action(PREFIX + "send_message", PREFIX) == "send"
    assert tool_action(PREFIX + "delete_draft", PREFIX) == "modify"


async def test_real_connection_errors_are_explained_instead_of_hidden():
    outcome = EmailTaskOutcome(status="failed", message="The Gmail connector requires authentication.")
    assert "requires authentication" in outcome_text(outcome, set())


async def test_published_report_is_injected_with_its_exact_bytes():
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        name = "ROL_2025_Finance_Report_October_30_13_12_40.html"
        content = b"<html>Published report</html>"
        (root / name).write_bytes(content)
        (root / "index.json").write_text(json.dumps([{"file": name, "generated": "2026-10-30 13:12:40"}]))
        contacts = root / "contacts.json"
        contacts.write_text(json.dumps({"aliases": {"Mom": "mom@example.org"}}))
        context = EmailRequestContext("Draft an email to Mom and attach the latest finance report", root, contacts)
        assert "mom@example.org" in context.notes and "outside the mailbox" in context.notes
        updated = context.tool_input(PREFIX + "create_draft", {"to": ["mom@example.org"]}, PREFIX)
        assert updated["attachments"][0]["filename"] == name
        assert base64.b64decode(updated["attachments"][0]["content"]) == content
        assert context.tool_input(PREFIX + "search_threads", {}, PREFIX) is None
        assert context.tool_input(PREFIX + "send_message", {"draftId": "existing"}, PREFIX) is None
        assert latest_report_attachment("Attach the latest 2026 finance report", root) is None
        assert latest_report_attachment("Draft an email but do not attach the latest finance report", root) is None
        (root / "index.json").write_text(json.dumps([{"file": "../secrets.html"}]))
        assert latest_report_attachment("Attach the latest finance report", root) is None


async def test_wrapper_returns_the_structured_clarification_after_successful_searches():
    from claude_agent_sdk import AssistantMessage, UserMessage, ToolUseBlock, ToolResultBlock, ResultMessage
    config = EmailConnectorSettings(provider="gmail", connector="claude_ai_Gmail")
    message = "Please confirm Mom's email address. The latest published finance report is ready to attach."
    async def query(**kwargs):
        assert kwargs["options"].output_format["type"] == "json_schema"
        yield AssistantMessage([ToolUseBlock("s1", PREFIX + "search_threads", {})], "fake")
        yield UserMessage([ToolResultBlock("s1", "matching mail", False)])
        yield ResultMessage("success", 1, 1, False, 1, "test", structured_output={
            "status": "needs_information", "message": message, "action": None})
    reply = await EmailConnectorRunner(lambda: config, query).run(
        YOLANDA.roster.role("email_tools"), "email_tools", "unused", "Draft an email to Mom")
    assert reply.text == message


async def test_attachment_hook_checks_authorization_before_injecting_bytes():
    from claude_agent_sdk import ResultMessage
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        name = "ROL_2025_Finance_Report_Test.html"
        (root / name).write_bytes(b"exact attachment bytes")
        (root / "index.json").write_text(json.dumps([{"file": name}]))
        context = EmailRequestContext("Draft an email and attach the latest finance report", root, root / "absent.json")
        async def query(prompt, options):
            hook = options.hooks["PreToolUse"][0].hooks[0]
            denied = await hook({"tool_name": PREFIX + "send_message", "tool_input": {}}, "send", {})
            assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
            updated = await hook({"tool_name": PREFIX + "create_draft", "tool_input": {"subject": "Report"}}, "draft", {})
            data = updated["hookSpecificOutput"]["updatedInput"]
            assert data["subject"] == "Report"
            assert base64.b64decode(data["attachments"][0]["content"]) == b"exact attachment bytes"
            yield ResultMessage("success", 1, 1, False, 1, "test", structured_output={
                "status": "needs_information", "message": "Please confirm the recipient.", "action": None})
        config = EmailConnectorSettings(provider="gmail", connector="claude_ai_Gmail")
        reply = await EmailConnectorRunner(lambda: config, query, lambda _: context).run(
            YOLANDA.roster.role("email_tools"), "email_tools", "unused", "Draft an email and attach the latest finance report")
        assert reply.text == "Please confirm the recipient."


if __name__ == "__main__":
    from tests.runner import main
    main(dict(globals()))
