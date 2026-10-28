"""Test doubles shared by the post office tests."""
from __future__ import annotations

from dataclasses import dataclass


class FakeClock:
    """Time advances only when somebody sleeps."""

    def __init__( self ) -> None:
        self.now = 0.0
        self.on_sleep = None    # optional callback: lets a test deliver a reply "meanwhile"

    def monotonic( self ) -> float:
        return self.now

    def sleep( self, seconds: float ) -> None:
        self.now += seconds
        if self.on_sleep is not None:
            self.on_sleep()


@dataclass
class FakeRoleResult:
    final_text: str
    is_error:   bool = False
    tool_calls: int = 3
    cost_usd:   float | None = 0.01


class FakeRoleRunner:
    """Stands in for finance_team's run_role: records the AgentRole it was given."""

    def __init__( self, text: str = "Built it; 12 tests passed.", is_error: bool = False ) -> None:
        self.calls: list[ dict ] = []
        self._text     = text
        self._is_error = is_error

    async def __call__( self, role, cwd: str, model: str, echo: bool = True ) -> FakeRoleResult:
        self.calls.append({ "role": role, "cwd": cwd, "model": model })
        return FakeRoleResult( final_text=self._text, is_error=self._is_error )
