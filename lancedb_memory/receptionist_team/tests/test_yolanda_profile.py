"""Offline email routing and provider-boundary regressions."""
from agent_memory.models import AgentReply
from tests.fakes import ScriptedModel
from agent_comms.adapters.json_registry import JsonAgentRegistry
from agent_comms.adapters.keyword_router import KeywordRouter
from agent_comms.factory import REGISTRY
from ..profiles import PROFILES, SYDNEY, TOYOTA, YOLANDA
from ..adapters.routing_model import RoutingModel
from ..adapters.email_connector_runner import EmailConnectorRunner
from ..email.config import EmailConnectorSettings
from ..email.tool_policy import EmailToolPolicy


def prompt(text):
    return f"USER: old email request\nAGENT: old result\nUSER: {text}\nAGENT:"


async def test_yolanda_is_the_shared_engine_with_her_own_email_roster():
    assert PROFILES["yolanda"] is YOLANDA
    assert YOLANDA.chat_model == TOYOTA.chat_model
    assert YOLANDA.default_session == "yolanda" and YOLANDA.nexus_subdir == "yolanda"
    assert YOLANDA.service_port == 8771
    assert set(YOLANDA.roster.names()) == {"email_tools"}
    assert isinstance(YOLANDA.build_runner(), EmailConnectorRunner)
    assert not YOLANDA.operations and not YOLANDA.serves_peer_memory
    assert YOLANDA.peer_session is None  # email contents are not peer recall for the finance desks


async def test_other_front_desks_forward_email_without_knowing_the_provider():
    for profile in [TOYOTA, SYDNEY]:
        current = prompt("Ask Yolanda to draft an email to board@example.org")
        route = profile.route_override
        assert route.forced_agent(current) == profile.name + "_post_office"
        assert route.question(current).startswith("yolanda:")
        assert route.forced_agent(prompt("hello")) is None
        assert route.forced_agent(prompt("Open Yolanda")) is None
        assert route.forced_agent(prompt("Switch me over to Yolanda")) is None
    card = JsonAgentRegistry(REGISTRY).card("yolanda")
    assert card.settings["url"].endswith(":8771")
    assert not card.capability("message").idempotent
    assert not card.capability("ask").idempotent
    request = KeywordRouter(JsonAgentRegistry(REGISTRY)).route("sydney", "yolanda: create a draft for the board")
    assert request.recipient == "yolanda" and request.task_type == "message"


async def test_post_office_tasks_and_mailbox_questions_bypass_the_chat_model():
    class Runner:
        calls = []
        async def run(self, role, name, model, extra):
            self.calls.append((name, extra));return AgentReply(text="actual email tool result")
    for text in ["Check Gmail connectivity", "Send the approved draft", "[Message for Yolanda from sydney, through the team post office, task t1.] Draft a reply."]:
        runner = Runner();runner.calls = []
        model = RoutingModel(ScriptedModel([]), "unused", YOLANDA.roster, runner, override=YOLANDA.route_override)
        assert (await model.complete(prompt(text))).text == "actual email tool result"
        assert runner.calls == [("email_tools", text)]


async def test_mail_tools_and_authorization_stay_inside_yolanda():
    prefix = "mcp__claude_ai_Gmail__"
    reading = EmailToolPolicy(prefix, "Read my unread emails")
    assert reading.problem(prefix + "search_threads") is None
    assert reading.problem(prefix + "list_drafts") is None
    assert reading.problem(prefix + "send_email")
    assert reading.problem("Bash") and reading.problem("mcp__other__send_email")
    drafting = EmailToolPolicy(prefix, "Draft a reply to this message")
    assert drafting.problem(prefix + "create_draft") is None
    assert drafting.problem(prefix + "send_email")
    assert drafting.problem(prefix + "delete_draft")
    for text in ["Do not send; just draft", "Please don't send this message", "Draft only"]:
        assert EmailToolPolicy(prefix, text).problem(prefix + "send_email")
    sending = EmailToolPolicy(prefix, "[Message for Yolanda from sydney, through the team post office, task t1.] Send the approved draft to board@example.org")
    assert sending.problem(prefix + "send_draft") is None
    assert sending.problem(prefix + "delete_message")
    assert (await reading.hook({"tool_name": prefix + "send_email"}, "x", {}))["hookSpecificOutput"]["permissionDecision"] == "deny"


async def test_provider_change_does_not_change_front_desk_routing():
    config = EmailConnectorSettings(provider="outlook", connector="claude_ai_Outlook")
    assert config.tool_prefix == "mcp__claude_ai_Outlook__"
    assert YOLANDA.route_override.forced_agent(prompt("Read my Outlook inbox")) == "email_tools"


async def test_email_runner_requires_real_provider_results():
    from claude_agent_sdk import AssistantMessage, UserMessage, ToolUseBlock, ToolResultBlock, ResultMessage
    config = EmailConnectorSettings(provider="gmail", connector="claude_ai_Gmail")
    seen = []
    async def fake_query(prompt, options):
        seen.append(options)
        yield AssistantMessage([ToolUseBlock("t1", config.tool_prefix + "list_labels", {})], "fake")
        yield UserMessage([ToolResultBlock("t1", "labels returned", False)])
        yield ResultMessage("success", 1, 1, False, 1, "test", result="Gmail connected.", total_cost_usd=0, structured_output={"status":"completed","message":"Gmail connected.","action":"read"})
    runner = EmailConnectorRunner(lambda: config, fake_query)
    reply = await runner.run(YOLANDA.roster.role("email_tools"), "email_tools", "unused", "Check Gmail connectivity")
    assert reply.text == "Gmail connected."
    assert seen[0].tools is None  # explicit tools=[ToolSearch] breaks native connector discovery
    assert seen[0].allowed_tools == ["ToolSearch", "StructuredOutput", config.tool_prefix + "*"]
    assert "PreToolUse" in seen[0].hooks
    async def fabricated_query(**kwargs):
        yield ResultMessage("success", 1, 1, False, 1, "test", result="Sent the email.")
    reply = await EmailConnectorRunner(lambda: config, fabricated_query).run(YOLANDA.roster.role("email_tools"), "email_tools", "unused", "Read Gmail")
    assert reply.text.startswith("No email action was confirmed.")
    assert "Sent the email" not in reply.text


if __name__ == "__main__":
    from tests.runner import main
    main(dict(globals()))
