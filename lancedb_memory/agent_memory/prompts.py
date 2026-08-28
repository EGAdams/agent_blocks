"""PromptComposer implementations."""
from pathlib import Path

from .models import Turn

DEFAULT_TEMPLATE = """You are a helpful assistant with long-term memory.

The following are excerpts from earlier conversation, recalled because they are relevant to what the user just said. They may be out of context or unrelated; use them only when they help.

<<CONVERSATION>>

USER: <<MESSAGE>>
AGENT:"""


class TemplatePromptComposer:
    """Fills <<CONVERSATION>> and <<MESSAGE>> -- same contract as prompt_response.txt."""

    def __init__( self, template: str = DEFAULT_TEMPLATE ) -> None:
        self._template = template

    @classmethod
    def from_file( cls, path: Path ) -> "TemplatePromptComposer":
        if not path.exists():
            return cls()
        return cls( path.read_text( encoding="utf-8" ))

    def compose( self, history: list[ Turn ], user_message: str ) -> str:
        conversation = "\n".join( f"{ turn.speaker }: { turn.message }" for turn in history )
        return (
            self._template
            .replace( "<<CONVERSATION>>", conversation )
            .replace( "<<MESSAGE>>", user_message )
        )
