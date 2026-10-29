"""MapActionRegistry: IActionRegistry."""
from __future__ import annotations

from ..interfaces.session import ICashyAction
from ..models import IntentKind


class MapActionRegistry:
    def __init__( self, actions: dict[ IntentKind, ICashyAction ], fallback: ICashyAction ) -> None:
        self._actions  = actions
        self._fallback = fallback

    def action( self, kind: IntentKind ) -> ICashyAction:
        return self._actions.get( kind, self._fallback )
