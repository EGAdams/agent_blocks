"""Maps a spa_documentation item path to its real .py source file in
agent_blocks. Shared by update_documentation_agent.py and server.py -- kept
dependency-free (stdlib only) so server.py doesn't need the claude_agent_sdk
venv just to serve static files.
"""
import re
from pathlib import Path

REPO_ROOT = Path( __file__ ).resolve().parent.parent
DOCS_ROOT = Path( __file__ ).resolve().parent

_SAFE_PART = re.compile( r"^[A-Za-z0-9_]+$" )

# Every item folder uses these same fixed basenames (see
# catherine_agent_sdk/basic_agent and .../future_agent) -- kept alongside
# resolve_source_path since both update_documentation_agent.py and
# server.py need to know which files make up an item's docs.
DOC_FILES = {
    "overview": "basic_agent.html",
    "source": "basic_agent.py.html",
    "class_diagram": "mermaid_class.html",
    "sequence_diagram": "mermaid_sequence.html",
    "construction_status": "basic_agent_construction_status.html",
    "report": "basic_agent_update_documentation.html",
}

# The subset of DOC_FILES that actually needs to reflect the source file's
# current content -- used to decide whether docs are stale/missing. overview,
# construction_status, and report are hand/agent-authored status text, not
# generated-from-source content (overview is empty even on the fully-built
# catherine_agent_sdk/basic_agent reference item), so they're excluded.
SOURCE_DERIVED_DOCS = ( "source", "class_diagram", "sequence_diagram" )

# Paths are relative to voice_communication/, which split into two package
# roots ( ts/ and py/ ) on 2026-09-04; the prefixes below are load-bearing.
VOICE_COMMUNICATION_SOURCE_FILES = {
    "interfaces": "ts/src/interfaces/index.ts",
    "typescript_contracts": "ts/src/typescript_contracts/contracts.ts",
    "pydantic_models": "py/src/voice_communication/pydantic_models/models.py",
    "input_draft": "ts/src/input_draft/input_draft.ts",
    "voice_session": "ts/src/voice_session/voice_session.ts",
    "conversation_agent": "ts/src/conversation_agent/conversation_agent.ts",
    "spoken_output_policy": "ts/src/spoken_output_policy/spoken_output_policy.ts",
    "conversation_coordinator": "ts/src/conversation_coordinator/conversation_coordinator.ts",
    "letta_agent_adapter": "ts/src/letta_agent_adapter/letta_agent_adapter.ts",
    "audio_capture": "py/src/voice_communication/audio_capture/audio_capture.py",
    "transcription_strategy": "py/src/voice_communication/transcription_strategy/transcription_strategy.py",
    "route_strategy": "ts/src/route_strategy/route_strategy.ts",
    "speech_synthesizer": "py/src/voice_communication/speech_synthesizer/speech_synthesizer.py",
    "note_command_channel": "ts/src/note_command_channel/note_command_channel.ts",
    "detection_interface": "py/src/voice_communication/detection_interface/detection_interface.py",
    "language_processor": "ts/src/language_processor/language_processor.ts",
    "design_protocol": "ts/src/design_protocol/design_protocol.ts",
    "pipecat_service_client": "ts/src/pipecat_service_client/pipecat_service_client.ts",
    "pipecat_local_service": "py/src/voice_communication/pipecat_local_service/pipecat_local_service.py",
    "pipeline_factory": "py/src/voice_communication/pipeline_factory/pipeline_factory.py",
    "toyota_voice_application": "ts/src/toyota_voice_application/toyota_voice_application.ts",
    "voice_health_observer": "ts/src/voice_health_observer/voice_health_observer.ts",
}


# Paths are relative to this repo's own browser_tests/, not to agent_blocks.
SPA_BROWSER_TEST_SOURCE_FILES = {
    "characterization_suite": "legacy-navigation.test.ts",
}


def docs_missing( doc_dir: Path ) -> bool:
    """True if any source-derived doc file is missing or empty."""
    return any(
        not ( doc_dir / DOC_FILES[ key ] ).exists() or ( doc_dir / DOC_FILES[ key ] ).stat().st_size == 0
        for key in SOURCE_DERIVED_DOCS
    )


def resolve_source_path( item_path: str ) -> Path | None:
    """Docs path -> real source .py path, or None if unmapped/invalid.

    Not fully mechanical -- catherine_agent_sdk items sit directly under
    catherine_agent_sdk/, while lancedb_memory items sit under
    lancedb_memory/agent_memory/ (with adapters/ and observers/ one level
    deeper). Every path segment is validated against _SAFE_PART first since
    item_path can arrive from an untrusted query string (server.py's
    /api/git-status endpoint) -- rejects anything with "/", "..", etc.
    """
    parts = item_path.strip( "/" ).split( "/" )
    if not parts or not all( _SAFE_PART.match( part ) for part in parts ):
        return None

    section, rest = parts[ 0 ], parts[ 1: ]

    if section == "claude_agent_adapter" and len( rest ) == 1:
        return REPO_ROOT / "claude_agent_adapter" / f"{rest[ 0 ]}.py"

    if section == "catherine_agent_sdk" and len( rest ) == 1:
        return REPO_ROOT / "catherine_agent_sdk" / f"{rest[ 0 ]}.py"

    # The one section whose source lives inside this repo rather than one
    # level up in agent_blocks: browser_tests/ is the SPA's own test suite,
    # so DOCS_ROOT is correct here and REPO_ROOT would be wrong. The docs
    # folder is spa_browser_tests/ because browser_tests/ is already taken
    # on disk by that source.
    if section == "spa_browser_tests" and len( rest ) == 1:
        source_name = SPA_BROWSER_TEST_SOURCE_FILES.get( rest[ 0 ] )
        if source_name:
            return DOCS_ROOT / "browser_tests" / source_name

    if section == "lancedb_memory":
        base = REPO_ROOT / "lancedb_memory" / "agent_memory"
        if len( rest ) == 2 and rest[ 0 ] in ( "adapters", "observers" ):
            return base / rest[ 0 ] / f"{rest[ 1 ]}.py"
        # models/ used to be one shared models.py (every models/* item pointed
        # at the same file); it's since been split into a models/ package with
        # one file per Pydantic class, so it now resolves the same way
        # adapters/observers do.
        if len( rest ) == 2 and rest[ 0 ] == "models":
            return base / "models" / f"{rest[ 1 ]}.py"
        if len( rest ) == 1:
            return base / f"{rest[ 0 ]}.py"

    if section == "voice_communication" and len( rest ) == 1:
        source_name = VOICE_COMMUNICATION_SOURCE_FILES.get( rest[ 0 ] )
        if source_name:
            return REPO_ROOT / "voice_communication" / source_name

    if section == "solid_agent_systems" and len( rest ) == 1:
        return REPO_ROOT / "solid_agent_systems" / rest[ 0 ] / "src" / f"{rest[ 0 ]}.py"

    return None
