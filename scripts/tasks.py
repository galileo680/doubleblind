import argparse
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
MIN_PYTHON = (3, 12)
PYTHON_PACKAGES = ("llm", "contract", "sandbox", "broker", "agent")
DASHBOARD = ROOT / "dashboard"
SPOKES = ROOT / "demo" / "spokes"
NODE_PROJECTS = (DASHBOARD, ROOT / "demo" / "parcelly")
SPOKES_MYPY_TARGETS = ("app", "tests", "seed.py")
MYPY_TARGETS = (
    *(f"packages/{name}/doubleblind_{name}" for name in PYTHON_PACKAGES),
    "scripts",
    "tests",
)
PENDING_TARGETS = {
    "reset": "M12",
    "demo-live": "M10",
    "demo-record": "M12",
    "demo-replay": "M12",
}


def run(command: Sequence[str | Path], cwd: Path = ROOT) -> None:
    parts = [str(part) for part in command]
    relative = cwd.relative_to(ROOT) if cwd != ROOT else Path(".")
    print(f"[{relative}] $ {' '.join(parts)}", flush=True)
    completed = subprocess.run(parts, cwd=cwd, check=False)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def venv_python() -> Path:
    if os.name == "nt":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def candidate_base_pythons() -> Iterator[list[str]]:
    if sys.version_info >= MIN_PYTHON:
        yield [sys.executable]
    launcher = shutil.which("py")
    if launcher:
        for version in ("3.13", "3.12"):
            yield [launcher, f"-{version}"]
    for name in ("python3.13", "python3.12", "python3"):
        found = shutil.which(name)
        if found:
            yield [found]


def find_base_python() -> list[str]:
    check = f"import sys; print(sys.version_info >= {MIN_PYTHON})"
    for candidate in candidate_base_pythons():
        probe = subprocess.run(
            [*candidate, "-c", check], capture_output=True, text=True, check=False
        )
        if probe.returncode == 0 and probe.stdout.strip() == "True":
            return candidate
    required = ".".join(str(part) for part in MIN_PYTHON)
    raise SystemExit(f"Python {required}+ is required to create the virtual environment.")


def ensure_venv() -> Path:
    if not venv_python().exists():
        run([*find_base_python(), "-m", "venv", VENV])
    return venv_python()


def require_venv() -> Path:
    python = venv_python()
    if not python.exists():
        raise SystemExit("Virtual environment not found. Run the 'install' task first.")
    return python


def npm() -> str:
    executable = shutil.which("npm")
    if executable is None:
        raise SystemExit("npm was not found on PATH. Install Node.js 24 LTS.")
    return executable


def run_npm_script(script: str, projects: Sequence[Path] = NODE_PROJECTS) -> None:
    for project in projects:
        run([npm(), "run", script], cwd=project)


def task_install() -> None:
    python = ensure_venv()
    run([python, "-m", "pip", "install", "--upgrade", "pip"])
    run([python, "-m", "pip", "install", "-r", "requirements-dev.txt"])
    editable: list[str] = []
    for name in PYTHON_PACKAGES:
        editable += ["-e", f"packages/{name}"]
    run([python, "-m", "pip", "install", *editable])
    run([python, "-m", "pip", "install", "-r", SPOKES / "requirements.txt"])
    for project in NODE_PROJECTS:
        command = "ci" if (project / "package-lock.json").exists() else "install"
        run([npm(), command], cwd=project)


def task_check_comments() -> None:
    run([require_venv(), "scripts/check_no_comments.py"])


def task_lint() -> None:
    python = require_venv()
    run([python, "-m", "ruff", "check", "."])
    run([python, "-m", "ruff", "format", "--check", "."])
    run([python, "-m", "mypy", *MYPY_TARGETS])
    run([python, "-m", "mypy", *SPOKES_MYPY_TARGETS], cwd=SPOKES)
    task_check_comments()
    run_npm_script("lint")
    run_npm_script("format:check")
    run_npm_script("typecheck", (DASHBOARD,))


def task_format() -> None:
    python = require_venv()
    run([python, "-m", "ruff", "check", "--fix", "."])
    run([python, "-m", "ruff", "format", "."])
    run_npm_script("format")


def task_test() -> None:
    python = require_venv()
    run([python, "-m", "pytest"])
    run([python, "-m", "pytest"], cwd=SPOKES)
    run_npm_script("test")


def task_golden() -> None:
    run([require_venv(), "-m", "pytest", "-m", "golden", "packages/contract", "packages/broker"])


def task_scripted_session() -> None:
    run([require_venv(), "scripts/scripted_session.py"])


def task_probe() -> None:
    run([require_venv(), "scripts/isolation_probe.py"])


def docker_compose(*arguments: str) -> None:
    docker = shutil.which("docker")
    if docker is None:
        raise SystemExit("docker is required for this task")
    run([docker, "compose", *arguments])


def task_up() -> None:
    docker_compose("up", "--detach", "--build", "broker")


def task_down() -> None:
    docker_compose("--profile", "party", "down")


def task_smoke_demo() -> None:
    run([require_venv(), "scripts/smoke_demo_apps.py"])


def task_build() -> None:
    run_npm_script("build", (DASHBOARD,))


def pending(target: str) -> Callable[[], None]:
    def report() -> None:
        milestone = PENDING_TARGETS[target]
        raise SystemExit(f"'{target}' becomes available in {milestone}.")

    return report


TASKS: dict[str, Callable[[], None]] = {
    "install": task_install,
    "lint": task_lint,
    "format": task_format,
    "test": task_test,
    "build": task_build,
    "check-comments": task_check_comments,
    "smoke-demo": task_smoke_demo,
    "golden": task_golden,
    "scripted-session": task_scripted_session,
    "probe": task_probe,
    "up": task_up,
    "down": task_down,
    **{target: pending(target) for target in PENDING_TARGETS},
}


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="DoubleBlind development tasks")
    parser.add_argument("task", choices=sorted(TASKS))
    arguments = parser.parse_args(argv)
    TASKS[arguments.task]()


if __name__ == "__main__":
    main()
