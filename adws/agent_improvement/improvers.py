"""Improvers: each writes one PromptPatch and nothing else. None of them scores, gates or activates.

    FileImprover        a patch a person (or an earlier run) wrote, read from a JSON file
    ClaudeCliImprover   `claude -p` with no tools, shown the permitted section, the training failures
                        and the evidence -- never the held-out tasks, the judge's rules or the gates

The patch is only a proposal: `IChangePolicy` decides whether it is allowed, in the engine.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Callable

from .models import ArmScore, Evidence, PromptPatch


class FileImprover:
    """Implements IImprover over a JSON file holding { find, replace, summary, expected_benefit }."""

    name = "file"

    def __init__( self, path: Path, author: str ):
        self._path, self._author = path, author

    def propose( self, artifact: str, permitted: str, failures: list[ ArmScore ],
                 evidence: list[ Evidence ]) -> PromptPatch:
        data = json.loads( self._path.read_text( encoding="utf-8" ))
        return PromptPatch( author=data.pop( "author", self._author ), **data )


_INSTRUCTIONS = """You improve one section of an AI receptionist's system prompt.

You will see the section, the checks the receptionist failed on training tasks, her replies, and
reference evidence. Write the smallest edit to THIS SECTION ONLY that would fix the failures.

Rules:
- Answer with exactly one JSON object and nothing else:
  {"find": "...", "replace": "...", "summary": "...", "expected_benefit": "..."}
- "find" is an exact substring of the section (copy it byte for byte, including line breaks), and it must
  occur once. "replace" is its replacement. Keep it short: under 600 extra characters.
- Do not add headings, new routes, or new placeholders, and do not remove {{...}} placeholders.
- Do not widen what she may do. Teach the form of a request; do not grant new authority.
"""


class ClaudeCliImprover:
    """Implements IImprover with the Claude CLI. No tools, no session saved, one answer."""

    name = "claude-cli"

    def __init__( self, *, claude: str = "claude", model: str = "sonnet", timeout_seconds: float = 300.0,
                  run_process: Callable[ ..., subprocess.CompletedProcess ] = subprocess.run ):
        self._claude, self._model, self._timeout, self._run_process = claude, model, timeout_seconds, run_process

    def propose( self, artifact: str, permitted: str, failures: list[ ArmScore ],
                 evidence: list[ Evidence ]) -> PromptPatch:
        prompt = "\n\n".join([
            _INSTRUCTIONS,
            "SECTION (the only text you may change):\n<<<\n" + permitted + "\n>>>",
            "FAILED CHECKS ON TRAINING TASKS:\n" + "\n".join(
                f"- task { f.task }: failed { ', '.join( f.failed )}; passed { ', '.join( f.passed ) or 'nothing' }"
                for f in failures ),
            "EVIDENCE:\n" + "\n\n".join( f"[{ e.source }]\n{ e.text }" for e in evidence ),
        ])
        argv = [ self._claude, "-p", "--model", self._model, "--tools", "", "--no-session-persistence",
                 "--output-format", "text" ]
        done = self._run_process( argv, input=prompt, capture_output=True, text=True, timeout=self._timeout )
        if done.returncode != 0:
            raise RuntimeError( f"the improver exited { done.returncode }: { ( done.stderr or done.stdout ).strip()[ :300 ]}" )
        data = _first_object( done.stdout )
        return PromptPatch( find=str( data.get( "find", "" )), replace=str( data.get( "replace", "" )),
                            summary=str( data.get( "summary", "" )), expected_benefit=str( data.get( "expected_benefit", "" )),
                            author=f"{ self.name }:{ self._model }" )


def _first_object( text: str ) -> dict:
    fenced = re.search( r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S )
    candidates = [ fenced.group( 1 )] if fenced else []
    start = text.find( "{" )
    if start >= 0:
        candidates.append( text[ start:text.rfind( "}" ) + 1 ])
    for raw in candidates:
        try:
            value = json.loads( raw )
        except ValueError:
            continue
        if isinstance( value, dict ):
            return value
    raise ValueError( "the improver did not answer with a JSON object" )
