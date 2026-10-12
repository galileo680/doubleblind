import hashlib
import hmac
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx

from doubleblind_contract.runtime import free_port

ROOT = Path(__file__).resolve().parent.parent
SPOKES = ROOT / "demo" / "spokes"
PARCELLY = ROOT / "demo" / "parcelly"
WEBHOOK_SECRET = "smoke-webhook-secret"
STARTUP_TIMEOUT_S = 30.0
WEBHOOK_TIMEOUT_S = 10.0
SPOKES_SERVER = (sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1")


@dataclass
class Report:
    failures: list[str] = field(default_factory=list)

    def check(self, name: str, passed: bool, detail: str = "") -> None:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}" + ("" if passed else f": {detail}"))
        if not passed:
            self.failures.append(name)


@dataclass(frozen=True)
class ReceivedWebhook:
    signature: str
    raw_body: bytes


class WebhookReceiver:
    def __init__(self) -> None:
        self.received: list[ReceivedWebhook] = []
        self.arrived = threading.Event()
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                signature = self.headers.get("X-Parcelly-Signature", "")
                receiver.received.append(ReceivedWebhook(signature, self.rfile.read(length)))
                receiver.arrived.set()
                self.send_response(200)
                self.end_headers()

            def log_message(self, format: str, *args: Any) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host!s}:{port}/webhooks/parcelly"

    def wait(self, timeout_s: float) -> ReceivedWebhook | None:
        return self.received[0] if self.arrived.wait(timeout_s) else None

    def __enter__(self) -> "WebhookReceiver":
        self.thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.server.shutdown()
        self.server.server_close()


def wait_until_healthy(process: subprocess.Popen[bytes], url: str) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT_S
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"process exited with code {process.returncode} before {url} was up")
        try:
            if httpx.get(url, timeout=1.0).status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"{url} was not healthy within {STARTUP_TIMEOUT_S:.0f} s")


@contextmanager
def running(
    name: str, command: list[str], cwd: Path, env: dict[str, str], health_url: str, log_dir: Path
) -> Iterator[None]:
    log_path = log_dir / f"{name}.log"
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            command, cwd=cwd, env={**os.environ, **env}, stdout=log, stderr=subprocess.STDOUT
        )
        try:
            wait_until_healthy(process, health_url)
            yield
        except Exception:
            log.flush()
            tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]
            print(f"--- last lines of {name} log ---", *tail, sep="\n")
            raise
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()


def shipment(
    reference: str, weight_g: int = 1200, size: str = "A", **address: str
) -> dict[str, Any]:
    return {
        "reference": reference,
        "receiver": {
            "name": "John Smith",
            "phone": "+447700900200",
            "address": {
                "street": "Long Street",
                "building": "5",
                "flat": "3",
                "postcode": "E1 6AN",
                "city": "London",
                "country": "GB",
                **address,
            },
        },
        "parcel": {"weight_g": weight_g, "size": size},
    }


def error_code(response: httpx.Response) -> str | None:
    try:
        code = response.json().get("code")
    except ValueError:
        return None
    return code if isinstance(code, str) else None


def check_parcelly(base_url: str, receiver: WebhookReceiver, report: Report) -> None:
    with httpx.Client(base_url=base_url, timeout=10.0) as client:
        partner = client.post("/__sandbox/partners", json={"name": "smoke"})
        partner.raise_for_status()
        auth = {"Authorization": f"Bearer {partner.json()['api_key']}"}

        heavy = client.post(
            "/v1/shipments", headers=auth, json=shipment("SMOKE-HEAVY", weight_g=25001, size="C")
        )
        report.check(
            "Parcelly: 25001 g -> 422 WEIGHT_LIMIT_EXCEEDED",
            heavy.status_code == 422 and error_code(heavy) == "WEIGHT_LIMIT_EXCEEDED",
            heavy.text,
        )

        for _ in range(2):
            client.post("/v1/shipments", headers=auth, json=shipment("SMOKE-DUP"))
        charges = client.get("/__sandbox/charges", params={"reference": "SMOKE-DUP"}).json()
        report.check(
            "Parcelly: two requests without Idempotency-Key -> two charges",
            charges.get("count") == 2,
            json.dumps(charges),
        )

        mismatch = client.post(
            "/v1/shipments",
            headers=auth,
            json=shipment("SMOKE-CITY", postcode="M1 1AE", city="London"),
        )
        report.check(
            "Parcelly: wrong postcode for city -> 422 POSTCODE_CITY_MISMATCH",
            mismatch.status_code == 422 and error_code(mismatch) == "POSTCODE_CITY_MISMATCH",
            mismatch.text,
        )

        client.post(
            "/v1/webhooks", headers=auth, json={"url": receiver.url, "secret": WEBHOOK_SECRET}
        ).raise_for_status()
        created = client.post(
            "/v1/shipments",
            headers={**auth, "Idempotency-Key": "SMOKE-HOOK"},
            json=shipment("SMOKE-HOOK"),
        )
        created.raise_for_status()
        client.post(
            f"/__sandbox/shipments/{created.json()['id']}/advance", json={"to": "DELIVERED"}
        ).raise_for_status()

    delivery = receiver.wait(WEBHOOK_TIMEOUT_S)
    if delivery is None:
        report.check("Parcelly: advance sends a signed webhook", False, "no webhook received")
        return
    digest = hmac.new(WEBHOOK_SECRET.encode(), delivery.raw_body, hashlib.sha256).hexdigest()
    event = json.loads(delivery.raw_body)
    report.check(
        "Parcelly: advance sends a signed webhook",
        hmac.compare_digest(delivery.signature, f"sha256={digest}")
        and event.get("status") == "DELIVERED"
        and event.get("reference") == "SMOKE-HOOK",
        f"signature={delivery.signature} body={delivery.raw_body!r}",
    )


def check_spokes(base_url: str, report: Report) -> None:
    with httpx.Client(base_url=base_url, timeout=10.0) as client:
        created = client.post(
            "/orders",
            json={
                "customer_name": "John Smith",
                "phone": "07700 900200",
                "address_line": "Flat 3, 5 Long Street, London E1 6AN",
                "items": [
                    {
                        "sku": "CITY-U3",
                        "name": "Spokes Urban 3 city bike",
                        "quantity": 1,
                        "unit_price_pence": 289900,
                        "unit_weight_kg": 14.5,
                    }
                ],
            },
        )
        report.check(
            "Spokes: create order keeps weight in kg",
            created.status_code == 201 and created.json().get("weight_kg") == 14.5,
            created.text,
        )
        if created.status_code != 201:
            return
        paid = client.post(f"/orders/{created.json()['id']}/pay")
        report.check(
            "Spokes: paying an order marks it paid",
            paid.status_code == 200 and paid.json().get("status") == "paid",
            paid.text,
        )


def main() -> None:
    node = shutil.which("node")
    if node is None:
        raise SystemExit("node was not found on PATH. Install Node.js 24 LTS.")
    parcelly_port, spokes_port = free_port(), free_port()
    report = Report()
    with (
        tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir,
        WebhookReceiver() as receiver,
    ):
        log_dir = Path(temp_dir)
        database_url = f"sqlite:///{(log_dir / 'spokes.db').as_posix()}"
        parcelly_url = f"http://127.0.0.1:{parcelly_port}"
        spokes_url = f"http://127.0.0.1:{spokes_port}"
        with (
            running(
                "parcelly",
                [node, "src/server.js"],
                PARCELLY,
                {"PORT": str(parcelly_port), "HOST": "127.0.0.1", "SANDBOX": "1"},
                f"{parcelly_url}/health",
                log_dir,
            ),
            running(
                "spokes",
                [*SPOKES_SERVER, "--port", str(spokes_port)],
                SPOKES,
                {"SPOKES_DATABASE_URL": database_url},
                f"{spokes_url}/health",
                log_dir,
            ),
        ):
            check_parcelly(parcelly_url, receiver, report)
            check_spokes(spokes_url, report)
    if report.failures:
        raise SystemExit(f"{len(report.failures)} smoke check(s) failed.")
    print("All smoke checks passed.")


if __name__ == "__main__":
    main()
