import argparse
import base64
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from typing import Any

HOP_BY_HOP = frozenset(
    {"host", "content-length", "connection", "transfer-encoding", "keep-alive", "accept-encoding"}
)
RETRY_DELAY_S = 1.0
FORWARD_TIMEOUT_S = 30.0


def encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def clean_headers(headers: Any) -> dict[str, str]:
    return {key: value for key, value in dict(headers).items() if key.lower() not in HOP_BY_HOP}


class RelayClient:
    def __init__(
        self, broker: str, session: str, party: str, token: str, target: str, wait_s: int = 25
    ) -> None:
        self.base = f"{broker.rstrip('/')}/relay/{session}/{party}"
        self.token = token
        self.target = target.rstrip("/")
        self.wait_s = wait_s
        self.stop = threading.Event()

    def fetch(self) -> dict[str, Any] | None:
        request = urllib.request.Request(
            f"{self.base}/next?wait={self.wait_s}", headers={"X-Relay-Token": self.token}
        )
        with urllib.request.urlopen(request, timeout=self.wait_s + 15) as response:
            if response.status == 204:
                return None
            item: dict[str, Any] = json.load(response)
            return item

    def forward(self, item: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.target}{item['path']}"
        if item.get("query"):
            url = f"{url}?{item['query']}"
        body = base64.b64decode(item.get("body_b64") or "")
        request = urllib.request.Request(
            url,
            data=body or None,
            method=item["method"],
            headers=clean_headers(item.get("headers") or {}),
        )
        try:
            with urllib.request.urlopen(request, timeout=FORWARD_TIMEOUT_S) as response:
                return {
                    "status": response.status,
                    "headers": clean_headers(response.headers),
                    "body_b64": encode(response.read()),
                }
        except urllib.error.HTTPError as error:
            return {
                "status": error.code,
                "headers": clean_headers(error.headers),
                "body_b64": encode(error.read()),
            }
        except OSError as error:
            payload = {"code": "RELAY_TARGET_UNAVAILABLE", "message": str(error)}
            return {
                "status": 502,
                "headers": {"Content-Type": "application/json"},
                "body_b64": encode(json.dumps(payload).encode()),
            }

    def reply(self, request_id: str, response: dict[str, Any]) -> None:
        request = urllib.request.Request(
            f"{self.base}/responses/{request_id}",
            data=json.dumps(response).encode(),
            method="POST",
            headers={"X-Relay-Token": self.token, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=FORWARD_TIMEOUT_S):
            return

    def handle(self, item: dict[str, Any]) -> None:
        try:
            self.reply(item["request_id"], self.forward(item))
        except OSError:
            return

    def serve(self) -> None:
        while not self.stop.is_set():
            try:
                item = self.fetch()
            except OSError:
                time.sleep(RETRY_DELAY_S)
                continue
            if item is not None:
                threading.Thread(target=self.handle, args=(item,), daemon=True).start()


def main() -> None:
    parser = argparse.ArgumentParser(description="Outbound tunnel from a sandbox to the gateway")
    parser.add_argument("--broker", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--party", required=True)
    parser.add_argument("--token", default=os.environ.get("DOUBLEBLIND_RELAY_TOKEN"))
    parser.add_argument("--target", required=True)
    parser.add_argument("--wait", type=int, default=25)
    parser.add_argument("--app", default=os.environ.get("DOUBLEBLIND_APP_COMMAND"))
    parser.add_argument("--app-cwd", default=os.environ.get("DOUBLEBLIND_APP_CWD", "."))
    arguments = parser.parse_args()
    if not arguments.token:
        parser.error("pass --token or set DOUBLEBLIND_RELAY_TOKEN")
    environment = {
        key: value for key, value in os.environ.items() if key != "DOUBLEBLIND_RELAY_TOKEN"
    }
    app = (
        subprocess.Popen(arguments.app, shell=True, cwd=arguments.app_cwd, env=environment)
        if arguments.app
        else None
    )
    try:
        RelayClient(
            arguments.broker,
            arguments.session,
            arguments.party,
            arguments.token,
            arguments.target,
            arguments.wait,
        ).serve()
    finally:
        if app is not None:
            app.terminate()


if __name__ == "__main__":
    main()
