import os
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, Literal
from urllib.parse import urlsplit

import httpx
from jsonpath_ng import parse as parse_jsonpath

from doubleblind_contract.models import AppSpec, Binding
from doubleblind_contract.templating import render

CallKind = Literal["action", "query"]
STARTUP_TIMEOUT_S = 30.0
STOP_TIMEOUT_S = 10.0
LOG_TAIL_LINES = 50
REQUEST_TIMEOUT_S = 15.0


class AppStartError(Exception):
    def __init__(self, message: str, log_tail: str) -> None:
        super().__init__(message)
        self.log_tail = log_tail


@dataclass
class CallResult:
    status: int
    body: Any
    text: str
    captured: dict[str, Any] = field(default_factory=dict)
    capture_problems: list[str] = field(default_factory=list)


def parse_response_body(response: httpx.Response) -> Any:
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        return response.text


def capture_values(body: Any, capture: Mapping[str, str]) -> tuple[dict[str, Any], list[str]]:
    values: dict[str, Any] = {}
    problems: list[str] = []
    for name, expression in capture.items():
        try:
            found = parse_jsonpath(expression).find(body)
        except Exception as error:
            problems.append(f"capture {name}: invalid JSONPath {expression}: {error}")
            continue
        if not found:
            problems.append(f"capture {name}: {expression} not found in the response")
            continue
        values[name] = found[0].value
    return values, problems


class PartyClient:
    def __init__(self, binding: Binding, http: httpx.Client) -> None:
        self.binding = binding
        self.http = http
        self.base_url = binding.base_url.rstrip("/")

    def call(self, kind: CallKind, name: str, variables: Mapping[str, Any]) -> CallResult:
        spec = (self.binding.actions if kind == "action" else self.binding.queries)[name]
        path = render(spec.path, variables)
        headers = render(spec.headers or {}, variables)
        query = render(spec.query or {}, variables)
        request: dict[str, Any] = {"headers": headers, "params": query}
        if spec.body is not None:
            request["json"] = render(spec.body, variables)
        response = self.http.request(spec.method, f"{self.base_url}{path}", **request)
        body = parse_response_body(response)
        result = CallResult(response.status_code, body, response.text)
        if spec.capture and response.status_code < 400:
            result.captured, result.capture_problems = capture_values(body, spec.capture)
        return result


def resolve_command(command: str, env: Mapping[str, str]) -> list[str]:
    arguments = shlex.split(command, posix=True)
    if not arguments:
        raise AppStartError("the start command is empty", "")
    executable = shutil.which(arguments[0], path=env.get("PATH"))
    if executable is not None:
        arguments[0] = executable
    return arguments


class ManagedApp:
    def __init__(
        self,
        name: str,
        spec: AppSpec,
        base_url: str,
        binding_dir: Path,
        env: Mapping[str, str],
        log_dir: Path,
    ) -> None:
        self.name = name
        self.spec = spec
        self.base_url = base_url.rstrip("/")
        self.cwd = (binding_dir / spec.cwd).resolve()
        self.env = dict(env)
        self.log_path = log_dir / f"{name}.log"
        self.process: subprocess.Popen[bytes] | None = None
        self.log_file: IO[bytes] | None = None

    def child_env(self, variables: Mapping[str, Any]) -> dict[str, str]:
        env = dict(self.env)
        env.update({key: str(value) for key, value in render(self.spec.env, variables).items()})
        interpreter_dir = str(Path(sys.executable).parent)
        env["PATH"] = os.pathsep.join([interpreter_dir, env.get("PATH", "")])
        env.setdefault("PYTHONUNBUFFERED", "1")
        return env

    def start(self, variables: Mapping[str, Any], timeout_s: float = STARTUP_TIMEOUT_S) -> None:
        env = self.child_env(variables)
        command = resolve_command(self.spec.start or "", env)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_file = self.log_path.open("wb")
        try:
            self.process = subprocess.Popen(
                command, cwd=self.cwd, env=env, stdout=self.log_file, stderr=subprocess.STDOUT
            )
        except OSError as error:
            raise AppStartError(f"{self.name}: cannot start {command[0]}: {error}", "") from error
        self.wait_until_healthy(timeout_s)

    @property
    def origin(self) -> str:
        parts = urlsplit(self.base_url)
        return f"{parts.scheme}://{parts.netloc}"

    def wait_until_healthy(self, timeout_s: float) -> None:
        healthcheck = self.spec.healthcheck
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                raise AppStartError(
                    f"{self.name}: the app exited with code {self.process.returncode}",
                    self.log_tail(),
                )
            if healthcheck is None:
                time.sleep(1.0)
                return
            try:
                response = httpx.request(
                    healthcheck.method, f"{self.origin}{healthcheck.path}", timeout=1.0
                )
                if response.status_code < 400:
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.2)
        raise AppStartError(
            f"{self.name}: no healthy answer from {self.origin} within {timeout_s:.0f} s",
            self.log_tail(),
        )

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=STOP_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if self.log_file is not None:
            self.log_file.close()
            self.log_file = None

    def log_tail(self) -> str:
        if self.log_file is not None:
            self.log_file.flush()
        if not self.log_path.exists():
            return ""
        content = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(content[-LOG_TAIL_LINES:])
