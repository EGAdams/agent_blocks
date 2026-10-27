"""Content-free timing records for local operational logs."""
from pydantic import BaseModel


class StageTiming( BaseModel ):
    request_id: str
    stage: str
    duration_ms: float
    outcome: str

