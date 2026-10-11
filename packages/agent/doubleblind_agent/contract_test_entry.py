import json
import subprocess
import sys
from pathlib import Path

OUTPUT_TAIL = 4000


def main() -> None:
    side, contract, binding, report, *extra = sys.argv[1:]
    completed = subprocess.run(
        ["db-contract", "test", side, contract, "--binding", binding, "--report", report, *extra],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    report_path = Path(report)
    outcome = {
        "exit_code": completed.returncode,
        "report": (
            json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else None
        ),
        "output": (completed.stdout + completed.stderr)[-OUTPUT_TAIL:],
    }
    print(json.dumps(outcome))


if __name__ == "__main__":
    main()
