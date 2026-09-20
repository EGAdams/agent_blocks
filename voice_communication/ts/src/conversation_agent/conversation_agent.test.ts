import { runConversationAgentContract } from "./conversation_agent.contract.js";
import { FakeConversationAgent } from "./fake_conversation_agent.js";

runConversationAgentContract( "FakeConversationAgent", () => new FakeConversationAgent() );
