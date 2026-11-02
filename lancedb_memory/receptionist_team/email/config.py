"""Provider selection contains no credentials; Claude owns authentication."""
import os
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field


class EmailConnectorSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(min_length=1)
    connector: str = Field(pattern=r"^[A-Za-z0-9_]+$")
    model: str = "sonnet"

    @property
    def tool_prefix(self) -> str:
        return f"mcp__{self.connector}__"


def load_settings() -> EmailConnectorSettings:
    path = Path(os.environ.get("YOLANDA_EMAIL_CONFIG", str(Path(__file__).with_name("provider.json"))))
    return EmailConnectorSettings.model_validate_json(path.read_text())
