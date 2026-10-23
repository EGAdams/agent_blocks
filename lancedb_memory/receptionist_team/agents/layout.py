"""DirectoryAgentLayout -- the one class that knows the folder names.

Single responsibility: translate "the user prompt template of this agent" into
a path. Every other module in this package asks the layout and never spells a
directory name itself, so renaming a folder is a one-file change.

The names below are exactly the directories the folders were created with,
`model_confiurations` included. The misspelling is preserved on purpose: it is
the user's layout, those folders already exist for toyota and toyota_mechanic,
and quietly "fixing" it would orphan them. If it is ever renamed, it is
renamed here and on disk in the same commit.
"""
from __future__ import annotations

from pathlib import Path

SYSTEM_PROMPT_DIR       = "system_prompt_template"
USER_PROMPT_DIR         = "user_prompt_template"
MODEL_CONFIGURATION_DIR = "model_confiurations"   # sic -- see module docstring
TOOLS_DIR               = "tools"
DOCUMENTATION_DIR       = "documentation"

# Used by the catalog to recognise an agent folder, and by `cli.py validate`
# to report which parts are missing.
PART_DIRS = (
    SYSTEM_PROMPT_DIR,
    USER_PROMPT_DIR,
    MODEL_CONFIGURATION_DIR,
    TOOLS_DIR,
    DOCUMENTATION_DIR,
)


class AmbiguousTemplate( RuntimeError ):
    """A prompt directory holds several markdown files and no canonical one."""


class DirectoryAgentLayout:
    """IAgentLayout over the `<agent>/<part>/` convention."""

    def system_prompt_dir( self, root: Path ) -> Path:
        return root / SYSTEM_PROMPT_DIR

    def user_prompt_dir( self, root: Path ) -> Path:
        return root / USER_PROMPT_DIR

    def model_configuration_dir( self, root: Path ) -> Path:
        return root / MODEL_CONFIGURATION_DIR

    def tools_dir( self, root: Path ) -> Path:
        return root / TOOLS_DIR

    def documentation_dir( self, root: Path ) -> Path:
        return root / DOCUMENTATION_DIR

    # --- file-level naming, same responsibility: where a thing lives ---------
    #
    # A prompt directory holds one markdown file. The canonical name is
    # preferred so a folder can also carry notes; otherwise the single .md
    # present is used, which keeps a hand-made folder working. Two .md files
    # and no canonical name is ambiguous, and ambiguity is reported rather
    # than guessed.

    def system_prompt_file( self, root: Path ) -> Path:
        return self._markdown_in( self.system_prompt_dir( root ), "system_prompt.md" )

    def user_prompt_file( self, root: Path ) -> Path:
        return self._markdown_in( self.user_prompt_dir( root ), "user_prompt.md" )

    def model_configuration_file( self, root: Path ) -> Path:
        return self.model_configuration_dir( root ) / "model.json"

    def tool_declaration_file( self, tool_root: Path ) -> Path:
        return tool_root / "tool.json"

    def _markdown_in( self, directory: Path, preferred: str ) -> Path:
        canonical = directory / preferred
        if canonical.is_file():
            return canonical
        candidates = sorted( directory.glob( "*.md" )) if directory.is_dir() else []
        if len( candidates ) == 1:
            return candidates[ 0 ]
        if len( candidates ) > 1:
            raise AmbiguousTemplate(
                f"{ directory } holds { len( candidates )} markdown files and no "
                f"{ preferred }: { [ c.name for c in candidates ] }"
            )
        # Nothing there: return the canonical path so the caller's
        # FileNotFoundError names the file that should have existed.
        return canonical
