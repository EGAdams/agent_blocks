"""HTTP Adapter to the dashboard Facade, not to its implementation objects."""
import asyncio
import json
from urllib.request import Request, urlopen
from uuid import uuid4

from ..models import WorkIntent, WorkReply, WorkView


class DashboardWorkflows:
    def __init__(self, base_url: str):
        self.url = base_url.rstrip('/') + '/api/receptionist-work'

    async def state(self) -> WorkView:
        def read():
            with urlopen(self.url, timeout=5) as response:
                payload = json.load(response)
            return WorkView.model_validate(payload['state'])
        return await asyncio.to_thread(read)

    async def execute(self, intent: WorkIntent, request_id: str | None = None) -> WorkReply:
        payload = {**intent.model_dump(), 'workspace': 'toyota', 'request_id': request_id or uuid4().hex}
        def send():
            request = Request(self.url, data=json.dumps(payload).encode(),
                              headers={'Content-Type': 'application/json'}, method='POST')
            with urlopen(request, timeout=100) as response:
                return WorkReply.model_validate_json(response.read())
        return await asyncio.to_thread(send)


class CallbackWorkObserver:
    def __init__(self, callback=None):
        self.callback = callback

    def publish(self, work: WorkReply) -> None:
        if self.callback:
            self.callback(work.model_dump(mode='json'))
