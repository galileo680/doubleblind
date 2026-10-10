import argparse
import re
import sys
from pathlib import Path
from typing import Any

from demo_workspace import REPO_ROOT

from doubleblind_contract.loader import parse_yaml
from doubleblind_contract.validate import validate_contract_data

CONTRACT = REPO_ROOT / "volumes" / "state" / "contract.yaml"
EXPECTED_MISMATCHES = [
    ("kg vs grams, rounding and minimum", r"gram|weight_g"),
    ("size A/B/C from weight", r"\bsize\b"),
    ("pallet above 25 kg", r"pallet"),
    ("one address line vs fields, unparsable addresses", r"address"),
    ("phone in E.164", r"phone|E\.164"),
    ("idempotency key", r"idempot"),
    ("status mapping incl. returned", r"return"),
    ("webhook security (HMAC, 401)", r"hmac|signature|signed"),
    ("postcode vs city", r"(?=.*(postcode|postal))(?=.*city)"),
]
REQUIRED = 8


def coverage(contract: dict[str, Any]) -> list[tuple[str, list[str], list[str]]]:
    rows = []
    for name, pattern in EXPECTED_MISMATCHES:
        decisions = [
            decision["id"]
            for decision in contract.get("decisions", [])
            if re.search(pattern, decision["text"], re.IGNORECASE | re.DOTALL)
        ]
        scenarios = [
            scenario["id"]
            for scenario in contract.get("scenarios", [])
            if set(decisions) & set(scenario.get("covers", []))
        ]
        rows.append((name, decisions, scenarios))
    return rows


def evaluate(contract: dict[str, Any]) -> bool:
    rows = coverage(contract)
    for name, decisions, scenarios in rows:
        mark = "OK  " if decisions and scenarios else "MISS"
        print(f"{mark} {name}: decisions {decisions or '-'}, scenarios {scenarios or '-'}")
    covered = sum(1 for _, decisions, scenarios in rows if decisions and scenarios)
    errors = validate_contract_data(contract).errors
    for finding in errors:
        print(f"INVALID {finding.render()}")
    print(f"covered {covered}/{len(rows)} expected mismatches, contract valid: {not errors}")
    return covered >= REQUIRED and not errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Score a negotiated contract")
    parser.add_argument("contract", type=Path, nargs="?", default=CONTRACT)
    arguments = parser.parse_args()
    if not evaluate(parse_yaml(arguments.contract.read_text(encoding="utf-8"))):
        sys.exit(1)


if __name__ == "__main__":
    main()
