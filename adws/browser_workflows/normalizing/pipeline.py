"""IActionNormalizer: the rules, in order. Adding a rewrite is adding a rule."""
from __future__ import annotations

from ..interfaces.normalizing import IConsequenceClassifier, IEventMapper, INormalizationRule, ISensitivityPolicy
from ..models import RawBrowserEvent, RecordedAction
from .rules import ( AddElementWaits, DropFieldFocusClicks, DropToggleClicks, EnsureStartsWithNavigation,
                     FlagConsequentialSteps, FoldFollowedNavigation, MapRawEvents, MergeTextEntries, RedactSecrets )


class RulePipelineNormalizer:
    def __init__( self, rules: list[ INormalizationRule ]) -> None:
        self._rules = list( rules )

    @property
    def rule_names( self ) -> list[ str ]:
        return [ r.name for r in self._rules ]

    def normalize( self, events: list[ RawBrowserEvent ]) -> list[ RecordedAction ]:
        actions: list[ RecordedAction ] = []
        for rule in self._rules:
            actions = rule.apply( actions, events )
        return actions


def default_rules( mapper: IEventMapper, policy: ISensitivityPolicy,
                   classifier: IConsequenceClassifier ) -> list[ INormalizationRule ]:
    return [
        MapRawEvents( mapper ),
        DropToggleClicks(),
        DropFieldFocusClicks(),
        MergeTextEntries(),
        FoldFollowedNavigation(),
        RedactSecrets( policy ),
        FlagConsequentialSteps( classifier ),
        EnsureStartsWithNavigation(),
        AddElementWaits(),
    ]
