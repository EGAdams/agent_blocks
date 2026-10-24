"""FirstRouteOpinion -- several IRouteOverrides behaving as one.

Composite, in the plain GoF sense: it implements IRouteOverride and holds
IRouteOverrides, so RoutingModel cannot tell whether it is talking to one
route or five, and `ReceptionistProfile.route_override` stays a single field.

Toyota needs this the moment she has two forced routes. A question about a
real figure must reach the books, and a request for a spreadsheet must reach
the spreadsheet maker, and neither can be left to the model -- but the
profile holds one override, and the obvious alternative (teach one route
about both agents) would put two unrelated sets of trigger words in one class
and lose the sentence at the top of each explaining what it is defending
against.

ORDER IS THE WHOLE BEHAVIOUR

First opinion wins. Members are asked in the order given, so the narrower
route goes first: a spreadsheet request is also, word for word, a question
about money, and the routes disagree about it on purpose. Putting
SpreadsheetRequestRoute ahead of FigureQuestionRoute makes "what did we spend
on utilities as a spreadsheet" a file rather than a spoken total, which is
what was asked for.

`question()` is answered by the SAME member that claimed the route, not by
the first member in the list. They happen to agree today -- both read the
latest user message -- but a route that forced an agent and then had another
route's text relayed to it would be a quiet, hard-to-see wrong, and the cost
of ruling it out is one extra call to forced_agent.
"""
from collections.abc import Sequence

from ..delegation import IRouteOverride


class FirstRouteOpinion:
    """Ask each route in turn; the first with an opinion decides the turn."""

    def __init__(self, routes: Sequence[IRouteOverride]) -> None:
        if not routes:
            # An empty composite would silently answer "no opinion" to
            # everything, which is indistinguishable from having no override
            # at all -- and profiles.py already says None for that.
            raise ValueError("FirstRouteOpinion needs at least one route; pass None instead.")
        self._routes = tuple(routes)

    def forced_agent(self, prompt: str) -> str | None:
        for route in self._routes:
            forced = route.forced_agent(prompt)
            if forced is not None:
                return forced
        return None

    def question(self, prompt: str) -> str:
        for route in self._routes:
            if route.forced_agent(prompt) is not None:
                return route.question(prompt)
        # No member claimed it, so nothing is being relayed; RoutingModel only
        # asks this after forced_agent returned a name. Answering with the
        # first member's reading keeps the method total rather than raising.
        return self._routes[0].question(prompt)
