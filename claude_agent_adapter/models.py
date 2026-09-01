"""Pydantic shapes yielded by ClaudeAgentAdapter.

A small local copy of the AgentEvent shape from
voice_communication/pydantic_models/models.py -- agent_blocks has no root
package, so each project directory (see the root CLAUDE.md's "Running"
section) stays self-contained rather than importing across siblings.
Reduced to the fields this adapter can populate on its own: submit() is not
given a session id, so AgentEvent here carries none either.
"""
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class AgentEventKind( str, Enum ):
    ASSISTANT_TEXT = "assistant_text"
    TERMINAL = "terminal"


class AgentEvent( BaseModel ):
    model_config = ConfigDict( frozen=True )

    generation: int = Field( ge=0 )
    kind: AgentEventKind
    text: str
