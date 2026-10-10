import json
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit


def header_value(headers: Mapping[str, str], name: str) -> str | None:
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None


@dataclass(frozen=True)
class IncomingRequest:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes


@dataclass(frozen=True)
class OutgoingResponse:
    status: int
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def json(cls, status: int, payload: Any) -> "OutgoingResponse":
        return cls(
            status,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            {"Content-Type": "application/json"},
        )


RequestHandler = Callable[[IncomingRequest], OutgoingResponse]


def handler_class(handle: RequestHandler) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def respond(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            request = IncomingRequest(
                method=self.command,
                path=urlsplit(self.path).path,
                headers={key: value for key, value in self.headers.items()},
                body=self.rfile.read(length) if length else b"",
            )
            try:
                response = handle(request)
            except Exception as error:
                response = OutgoingResponse.json(
                    500, {"code": "INTERNAL_ERROR", "message": f"{type(error).__name__}: {error}"}
                )
            self.send_response(response.status)
            for name, value in response.headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(response.body)))
            self.end_headers()
            if response.body:
                self.wfile.write(response.body)

        do_GET = respond
        do_POST = respond
        do_PUT = respond
        do_PATCH = respond
        do_DELETE = respond

        def log_message(self, format: str, *args: Any) -> None:
            return

    return Handler


class BackgroundServer:
    def __init__(self, handle: RequestHandler, port: int = 0) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", port), handler_class(handle))
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host!s}:{port}"

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def __enter__(self) -> "BackgroundServer":
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()
