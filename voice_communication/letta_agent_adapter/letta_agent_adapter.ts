import type { IConversationAgent } from "../conversation_agent/conversation_agent.js";

/**
 * Adapter role for Letta.
 *
 * The concrete adapter will implement IConversationAgent when this block
 * becomes active; callers depend only on the inherited port.
 */
export interface ILettaAgentAdapter extends IConversationAgent {}
