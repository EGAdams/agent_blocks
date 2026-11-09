"""IDocumentClassifier over Claude Haiku, through the Claude Agent SDK.

Only reached when the rules are under 0.90. Haiku gets the OCR text, the
folders that already exist, where EG filed the pages Scala asked about
before (`scala.questions` lessons), and the scan's path with the `Read` tool so it
can look at the image itself when the OCR is thin. It answers one JSON
object; anything else is UNSURE, and the scan waits in the inbox.

Runs on the local Claude Code subscription: ANTHROPIC_API_KEY and
ANTHROPIC_BASE_URL are blanked for the SDK, same as every adapter here.
The prompts are the `scala` agent folder's, handed in by the composition
root, so this module never reads the filesystem for them.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Callable

from .models import FINANCE, NOT_ASKED, UNSURE, Classification, Method

DEFAULT_MODEL = "claude-haiku-5-5"
MAX_OCR_CHARS = 4000
#: The SDK caps one stream message at 1 MiB by default, and Read on a 1.3 MB scan
#: returns the image base64-encoded inside one message -- over the cap, so the
#: call died with CLIJSONDecodeError and the scan went to the inbox unread.
MAX_BUFFER_BYTES = 32 * 1024 * 1024


class HaikuClassifier:
    def __init__( self,
                  system_prompt: str,
                  render_task: Callable[[ dict[ str, str ]], str ],
                  model: str = DEFAULT_MODEL,
                  max_turns: int = 4,
                  timeout_sec: float = 180.0,
                  lessons: Callable[[], str ] | None = None ) -> None:
        self._system_prompt = system_prompt
        self._lessons = lessons or ( lambda: "- (none yet)" )
        self._render_task = render_task
        self._model = model
        self._max_turns = max_turns
        self._timeout = timeout_sec

    def classify( self, text: str, image: Path, folders: tuple[ str, ... ]) -> Classification:
        task = self._render_task({
            "image_path": str( image ),
            "folders":    "\n".join( f"- { f }" for f in folders ) or "- (none yet)",
            "ocr_text":   text.strip()[ :MAX_OCR_CHARS ] or "(OCR found no text -- read the image)",
            "lessons":    self._lessons(),
        })
        try:
            reply = asyncio.run( asyncio.wait_for( self._ask( task ), self._timeout ))
        except Exception as exc:  # noqa: BLE001 -- a failed call is an answer, not a crash
            return Classification( folder=UNSURE, confidence=0.0, method=Method.HAIKU, answered=False,
                                   reason=f"{ NOT_ASKED }: { type( exc ).__name__ }: { exc }"[ :300 ])
        return parse_reply( reply )

    async def _ask( self, task: str ) -> str:
        from claude_agent_sdk import ( AssistantMessage, ClaudeAgentOptions, ResultMessage,
                                       TextBlock, query )
        options = ClaudeAgentOptions(
            model=self._model,
            max_turns=self._max_turns,
            system_prompt=self._system_prompt,
            setting_sources=[],
            allowed_tools=[ "Read" ],
            permission_mode="bypassPermissions",
            max_buffer_size=MAX_BUFFER_BYTES,
            env={ "ANTHROPIC_API_KEY": "", "ANTHROPIC_BASE_URL": "" },
        )
        chunks: list[ str ] = []
        final: ResultMessage | None = None
        async for message in query( prompt=task, options=options ):
            if isinstance( message, AssistantMessage ):
                chunks.extend( b.text for b in message.content if isinstance( b, TextBlock ))
            elif isinstance( message, ResultMessage ):
                final = message
        if final is None:
            raise RuntimeError( "no ResultMessage from the Agent SDK" )
        if final.is_error:
            raise RuntimeError( f"Agent SDK error: { final.result }" )
        return ( final.result or "" ).strip() or "\n".join( chunks )


def parse_reply( reply: str ) -> Classification:
    """The last JSON object in Haiku's reply, as a Classification. Never raises."""
    found = re.findall( r"\{[^{}]*\}", reply or "" )
    if not found:
        return Classification( folder=UNSURE, confidence=0.0, method=Method.HAIKU,
                               reason="Haiku did not answer with JSON" )
    try:
        payload = json.loads( found[ -1 ])
        folder = str( payload.get( "folder" ) or UNSURE ).strip().strip( "/" ).lower()
        confidence = max( 0.0, min( 1.0, float( payload.get( "confidence" ) or 0.0 )))
        reason = str( payload.get( "reason" ) or "" )[ :300 ]
    except ( ValueError, TypeError, AttributeError ):
        return Classification( folder=UNSURE, confidence=0.0, method=Method.HAIKU,
                               reason="Haiku's JSON could not be read" )
    if folder in ( "receipt", "statement", "rol_receipts" ):
        folder = FINANCE
    return Classification( folder=folder, confidence=confidence, method=Method.HAIKU, reason=reason )
