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
import re

# The composed prompt ends "USER: <message>\nAGENT:" (agent_memory/prompts.py's
# DEFAULT_TEMPLATE). Recalled turns above are labelled the same way, so take
# the LAST one -- that is the message being answered now.
# The leading ".*" is greedy on purpose: it eats every earlier USER: so the
# capture starts at the last one.
_LATEST_USER = re.compile(r".*USER:\s*(.*?)\s*\nAGENT:\s*$", re.DOTALL)


def latest_user_message(prompt: str) -> str:
    """The latest user message, or "" when the prompt carries no marker.

    Returning "" rather than the whole prompt on a miss is the load-bearing
    part. A fallback to the full text would make every turn look like whatever
    the route is watching for, as soon as one recalled line mentioned it --
    no marker, no forced route, and the model decides as it did before.
    """
    found = _LATEST_USER.search(prompt)
    return found.group(1) if found else ""
