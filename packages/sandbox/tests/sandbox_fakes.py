import base64
import json
from typing import Any

import httpx


class FakeSandboxApi:
    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.instances: list[dict[str, Any]] = []
        self.operations: dict[str, dict[str, Any]] = {}
        self.polls: dict[str, int] = {}
        self.tags: dict[str, str] = {}
        self.downloads: dict[tuple[str, str], bytes] = {}
        self.deleted: list[str] = []
        self.exit_code = 0
        self.stdout = "ok"
        self.encoding = "ascii"
        self.cost = 0.0012
        self.status = "SUCCESS"
        self.pending_polls = 1
        self.import_status = "SUCCESS"

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/sandboxes")
        if path.startswith("/v1/files/"):
            digest = path.removeprefix("/v1/files/")
            if digest in self.files:
                return httpx.Response(200, json={"uuid": self.files[digest], "sha256": digest})
            return httpx.Response(404, json={"detail": "not found"})
        if path == "/v1/files" and request.method == "POST":
            import hashlib

            digest = hashlib.sha256(request.content).hexdigest()
            self.files[digest] = f"file-{len(self.files) + 1}"
            return httpx.Response(200, json={"uuid": self.files[digest], "sha256": digest})
        if path == "/v1/instances":
            body = json.loads(request.content)
            self.instances.append(body)
            operation = f"op-{len(self.instances)}"
            self.operations[operation] = body
            return httpx.Response(200, json={"uuid": operation})
        if path == "/v1/images/import":
            body = json.loads(request.content)
            operation = f"import-{len(self.operations) + 1}"
            self.operations[operation] = {"import": body}
            if self.import_status == "SUCCESS":
                self.tags[body["tag"]] = "image-imported"
            return httpx.Response(200, json={"uuid": operation})
        if path.startswith("/v1/operations/"):
            operation = path.removeprefix("/v1/operations/")
            if request.method == "DELETE":
                self.deleted.append(operation)
                return httpx.Response(200, json={})
            return httpx.Response(200, json=self.operation_state(operation))
        if path == "/v1/inspect/":
            tag = request.url.params.get("tag", "")
            if tag in self.tags:
                return httpx.Response(302, headers={"Location": f"{self.tags[tag]}/"})
            return httpx.Response(404, json={"detail": "no such tag"})
        if path.startswith("/v1/inspect/") and path.count("/") == 4 and path.endswith("/"):
            uuid = path.split("/")[3]
            return httpx.Response(200, json={"uuid": uuid})
        if path.startswith("/v1/inspect/") and path.endswith("/download"):
            uuid = path.split("/")[3]
            content = self.downloads.get((uuid, request.url.params.get("path", "")))
            if content is None:
                return httpx.Response(404, json={"detail": "missing"})
            return httpx.Response(200, content=content)
        if path.startswith("/v1/images/") and path.endswith("/tag"):
            uuid = path.split("/")[3]
            self.tags[json.loads(request.content)["tag"]] = uuid
            return httpx.Response(200, json={"uuid": uuid})
        return httpx.Response(404, json={"detail": f"unexpected {request.method} {path}"})

    def operation_state(self, operation: str) -> dict[str, Any]:
        self.polls[operation] = self.polls.get(operation, 0) + 1
        if self.polls[operation] <= self.pending_polls:
            return {"status": "EXECUTING", "metadata": {}}
        if operation.startswith("import-"):
            return {"status": self.import_status, "error": "registry unreachable"}
        body = self.operations[operation]
        value = self.stdout
        if self.encoding == "base64":
            value = base64.b64encode(self.stdout.encode()).decode()
        image = None if body["disposable"] else f"image-{operation}"
        return {
            "status": self.status,
            "error": "worker crashed" if self.status == "FAILED" else None,
            "metadata": {
                "result": {
                    "state": {"exit_code": self.exit_code, "timed_out": False},
                    "stdout": {"value": value, "encoding": self.encoding},
                    "stderr": {"value": "", "encoding": "ascii"},
                    "resources": {"cost": self.cost},
                }
            }
            if self.status == "SUCCESS"
            else {},
            "result": {"image": image, "tag": None},
        }
