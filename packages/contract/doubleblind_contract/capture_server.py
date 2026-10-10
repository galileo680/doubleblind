import json
import threading
from dataclasses import dataclass
from typing import Any

from doubleblind_contract.http_server import BackgroundServer, IncomingRequest, OutgoingResponse


@dataclass
class CapturedWebhook:
    method: str
    path: str
    headers: dict[str, str]
    raw_body: bytes
    body: Any


class WebhookCapture:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.captured: list[CapturedWebhook] = []
        self.server = BackgroundServer(self.handle)

    @property
    def url(self) -> str:
        return f"{self.server.url}/webhooks"

    def start(self) -> None:
        self.server.start()

    def stop(self) -> None:
        self.server.stop()

    def __enter__(self) -> "WebhookCapture":
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    def reset(self) -> None:
        with self.lock:
            self.captured.clear()

    def webhooks(self) -> list[CapturedWebhook]:
        with self.lock:
            return list(self.captured)

    def handle(self, request: IncomingRequest) -> OutgoingResponse:
        try:
            body: Any = json.loads(request.body) if request.body.strip() else None
        except ValueError:
            body = request.body.decode("utf-8", errors="replace")
        webhook = CapturedWebhook(
            request.method, request.path, dict(request.headers), request.body, body
        )
        with self.lock:
            self.captured.append(webhook)
        return OutgoingResponse.json(200, {"status": "captured"})
