"""CityTaxDeskRoute -- what EG says to Liz about the city return goes to her desk, not the model.

The same failure every receptionist route exists for: asked "what are we
missing?", a model answers from recalled conversation. Liz's desk reads the
sentence with `city_tax.desk.read_request` (no model) and this route forces
it there whenever that names an intent. A greeting -- intent UNKNOWN -- stays
with the model. Only the latest user message is read, never recalled turns.
"""
from city_tax.desk import Intent, read_request

from .latest_user import latest_user_message


class CityTaxDeskRoute:
    def __init__(self, agent_name: str = "city_tax_desk") -> None:
        self._agent = agent_name

    def forced_agent(self, prompt: str) -> str | None:
        text = latest_user_message(prompt)
        if not text:
            return None
        return self._agent if read_request(text).intent is not Intent.UNKNOWN else None

    def question(self, prompt: str) -> str:
        return latest_user_message(prompt)
