"""INormalizationRule: each class makes one rewrite of the action list.

They run in the order `pipeline.default_rules()` lists them; every rule takes
the list as the previous one left it and returns a new list, never mutating
its input.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from ..interfaces.normalizing import IConsequenceClassifier, IEventMapper, ISensitivityPolicy
from ..models import RawBrowserEvent, RecordedAction, WaitCondition

BUTTON_INPUT_TYPES  = ( "submit", "button", "image", "reset", "file" )
FOLDED_TRANSITIONS  = ( "link", "form_submit", "client_redirect", "server_redirect" )
TOGGLE_INPUT_TYPES  = ( "checkbox", "radio" )


class MapRawEvents:
    name = "map_raw_events"

    def __init__( self, mapper: IEventMapper ) -> None:
        self._mapper = mapper

    def apply( self, actions: list[ RecordedAction ], events: list[ RawBrowserEvent ]) -> list[ RecordedAction ]:
        mapped = [ self._mapper.map( e ) for e in events ]
        return actions + [ a for a in mapped if a is not None ]


class DropFieldFocusClicks:
    """A click into a text field or a select is how a person gets there, not a
    step: playback types into (or chooses from) the field directly. Buttons
    that happen to be <input> elements -- submit, button, image -- are kept."""
    name = "drop_field_focus_clicks"

    def apply( self, actions: list[ RecordedAction ], events: list[ RawBrowserEvent ]) -> list[ RecordedAction ]:
        return [ a for a in actions if not ( a.action_type == "click" and _is_entry_field( a ))]


class DropToggleClicks:
    """A checkbox/radio click and the set_checked it caused are one change;
    set_checked is the one that replays the state rather than flipping it."""
    name = "drop_toggle_clicks"

    def apply( self, actions: list[ RecordedAction ], events: list[ RawBrowserEvent ]) -> list[ RecordedAction ]:
        kept: list[ RecordedAction ] = []
        for i, action in enumerate( actions ):
            if action.action_type == "click" and action.metadata.get( "input_type" ) in TOGGLE_INPUT_TYPES:
                key = action.metadata.get( "element_key" )
                if any( a.action_type == "set_checked" and _same_element( a, key ) for a in actions[ i + 1: i + 3 ]):
                    continue
            kept.append( action )
        return kept


class MergeTextEntries:
    """Several committed entries into one field in a row: only the last value counts."""
    name = "merge_text_entries"

    def apply( self, actions: list[ RecordedAction ], events: list[ RawBrowserEvent ]) -> list[ RecordedAction ]:
        kept: list[ RecordedAction ] = []
        for action in actions:
            if ( kept and action.action_type == "enter_text" and kept[ -1 ].action_type == "enter_text"
                    and _same_element( kept[ -1 ], action.metadata.get( "element_key" ))):
                kept[ -1 ] = action
                continue
            kept.append( action )
        return kept


class FoldFollowedNavigation:
    """A navigation the page caused (a link, a form, a redirect) is not a step
    of its own: it becomes the previous step's `wait_after`. Only a navigation
    the person caused directly (typed, bookmark, reload) is a `navigate` step.
    A navigate to where the previous step already is, is dropped."""
    name = "fold_followed_navigation"

    def apply( self, actions: list[ RecordedAction ], events: list[ RawBrowserEvent ]) -> list[ RecordedAction ]:
        kept: list[ RecordedAction ] = []
        for action in actions:
            if action.action_type != "navigate":
                kept.append( action )
                continue
            transition = action.metadata.get( "transition_type", "" )
            if kept and kept[ -1 ].action_type == "navigate" and _page( kept[ -1 ].url ) == _page( action.url ):
                continue
            if kept and ( transition in FOLDED_TRANSITIONS or kept[ -1 ].action_type == "navigate" ):
                previous = kept[ -1 ]
                waits = [ w for w in previous.wait_after if w.kind != "url_contains" ]
                waits.append( WaitCondition( kind="url_contains", value=_page( action.url )))
                waits.append( WaitCondition( kind="navigation_complete" ))
                kept[ -1 ] = previous.model_copy( update={ "wait_after": _unique( waits )})
                continue
            kept.append( action )
        return kept


class RedactSecrets:
    """The content script already dropped secrets; this checks again, here,
    against the same rules, so a bug on one side cannot leak one."""
    name = "redact_secrets"

    def __init__( self, policy: ISensitivityPolicy ) -> None:
        self._policy = policy

    def apply( self, actions: list[ RecordedAction ], events: list[ RawBrowserEvent ]) -> list[ RecordedAction ]:
        out: list[ RecordedAction ] = []
        for action in actions:
            if action.action_type in ( "enter_text", "select_option" ):
                reason = self._policy.reason( str( action.metadata.get( "input_type", "" )), action.metadata.get( "field_hints", {}))
                if reason or action.value_is_secret:
                    meta = { **action.metadata, "secret_reason": action.metadata.get( "secret_reason" ) or reason }
                    action = action.model_copy( update={ "value": None, "value_is_secret": True, "metadata": meta })
            out.append( action )
        return out


class FlagConsequentialSteps:
    name = "flag_consequential_steps"

    def __init__( self, classifier: IConsequenceClassifier ) -> None:
        self._classifier = classifier

    def apply( self, actions: list[ RecordedAction ], events: list[ RawBrowserEvent ]) -> list[ RecordedAction ]:
        out: list[ RecordedAction ] = []
        for action in actions:
            reason = self._classifier.reason( action )
            if reason:
                action = action.model_copy( update={ "requires_approval": True,
                                                     "metadata": { **action.metadata, "approval_reason": reason }})
            out.append( action )
        return out


class EnsureStartsWithNavigation:
    """Playback always begins on the page the recording began on."""
    name = "ensure_starts_with_navigation"

    def apply( self, actions: list[ RecordedAction ], events: list[ RawBrowserEvent ]) -> list[ RecordedAction ]:
        if not actions or actions[ 0 ].action_type == "navigate":
            return actions
        start = next(( e.url for e in events if e.url ), actions[ 0 ].url )
        if not start:
            return actions
        return [ RecordedAction( action_type="navigate", url=start, metadata={ "transition_type": "start" })] + actions


class AddElementWaits:
    """Every step that acts on an element waits for it to be visible first --
    an explicit condition, so playback never sleeps on a guess."""
    name = "add_element_waits"

    def apply( self, actions: list[ RecordedAction ], events: list[ RawBrowserEvent ]) -> list[ RecordedAction ]:
        out: list[ RecordedAction ] = []
        for action in actions:
            if action.locators and "wait_before" not in action.metadata:
                action = action.model_copy( update={ "metadata": { **action.metadata, "wait_before": "element_visible" }})
            out.append( action )
        return out


def _is_entry_field( action: RecordedAction ) -> bool:
    tag = action.metadata.get( "tag" )
    if tag in ( "textarea", "select" ):
        return True
    return tag == "input" and action.metadata.get( "input_type" ) not in ( *BUTTON_INPUT_TYPES, *TOGGLE_INPUT_TYPES )


def _same_element( action: RecordedAction, key: str | None ) -> bool:
    return bool( key ) and action.metadata.get( "element_key" ) == key


def _page( url: str ) -> str:
    parts = urlsplit( url )
    return f"{ parts.scheme }://{ parts.netloc }{ parts.path }" if parts.scheme else url


def _unique( waits: list[ WaitCondition ]) -> list[ WaitCondition ]:
    seen, out = set(), []
    for w in waits:
        if ( w.kind, w.value ) not in seen:
            seen.add(( w.kind, w.value ))
            out.append( w )
    return out
