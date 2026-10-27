"""latest_user_message -- read the message being answered out of a composed prompt.

Two IRouteOverride implementations need the same fact, and it is a fact that
is easy to get subtly wrong: the composed prompt holds the whole recalled
transcript above the current turn, every line of it labelled "USER:" exactly
like the live one. A route that searched the whole prompt would fire as soon
as any *remembered* turn mentioned money or status, which is the opposite of
what a route override is for.

So the extraction lives here once, and both status_route.py and
figure_route.py read it from this module rather than keeping a regex each.
"""
import json


def latest_user_message(prompt: str) -> str:
    """The latest user message, or "" when the prompt carries no marker.

    Returning "" rather than the whole prompt on a miss is the load-bearing
    part. A fallback to the full text would make every turn look like whatever
    the route is watching for, as soon as one recalled line mentioned it --
    no marker, no forced route, and the model decides as it did before.
    """
    # WorkflowModel appends capabilities after the legacy AGENT: delimiter.
    # Its final JSON envelope is authoritative; never scan the recalled turns
    # or the examples in those capabilities for a replacement user message.
    _, marker, tail = prompt.rpartition('\nLATEST USER MESSAGE: ')
    if marker:
        if not tail.endswith('\nTOYOTA:'):
            return ''
        try:
            value = json.loads(tail[:-len('\nTOYOTA:')])
        except (ValueError, TypeError):
            return ''
        return value['text'] if isinstance(value, dict) and isinstance(value.get('text'), str) else ''

    # Linear string operations also make a large nonmatching prompt cheap.
    # The former unanchored, greedy regex backtracked across every USER line
    # when the workflow suffix made its end-of-string condition impossible.
    legacy = prompt.rstrip()
    if not legacy.endswith('\nAGENT:'):
        return ''
    legacy = legacy[:-len('\nAGENT:')]
    _, marker, message = ('\n' + legacy).rpartition('\nUSER:')
    return message.strip() if marker else ''
