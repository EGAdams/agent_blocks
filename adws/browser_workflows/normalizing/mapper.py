"""IEventMapper: one raw event to one playback action, before any tidying."""
from __future__ import annotations

from ..models import RawBrowserEvent, RecordedAction


def element_key( event: RawBrowserEvent ) -> str:
    """What identifies 'the same element' across events: its best locator."""
    if not event.locators:
        return ""
    best = min( event.locators, key=lambda l: l.rank )
    return f"{ best.strategy }:{ best.value }"


class RawEventMapper:
    def map( self, event: RawBrowserEvent ) -> RecordedAction | None:
        meta = { "element_key": element_key( event ), "tag": event.tag, "input_type": event.input_type,
                 "text": event.text[ :120 ], "field_hints": dict( event.field_hints ), "title": event.title }
        common = { "url": event.url, "locators": list( event.locators ), "metadata": meta }
        if event.kind == "click":
            return RecordedAction( action_type="click", **common )
        if event.kind == "text_committed":
            secret = event.sensitive or event.value is None
            return RecordedAction( action_type="enter_text", value=None if secret else event.value,
                                   value_is_secret=secret, **_with_reason( common, event ))
        if event.kind == "select":
            return RecordedAction( action_type="select_option", value=None if event.sensitive else event.value,
                                   value_is_secret=event.sensitive, **_with_reason( common, event ))
        if event.kind == "check":
            return RecordedAction( action_type="set_checked", value="true" if event.checked else "false", **common )
        if event.kind == "key":
            return RecordedAction( action_type="press_key", value=event.key, **common )
        if event.kind == "navigation":
            return RecordedAction( action_type="navigate", url=event.url,
                                   metadata={ "transition_type": event.transition_type, "title": event.title })
        if event.kind == "tab_switch":
            return RecordedAction( action_type="switch_tab", url=event.url,
                                   metadata={ "tab_id": event.tab_id, "title": event.title })
        return None


def _with_reason( common: dict, event: RawBrowserEvent ) -> dict:
    if event.sensitive_reason:
        common = { **common, "metadata": { **common[ "metadata" ], "secret_reason": event.sensitive_reason }}
    return common
