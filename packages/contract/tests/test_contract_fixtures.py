import difflib
import re

from contract_fixtures import MINIMAL, SCHEMA_BROKEN, SEMANTIC_FIXTURES, SIGNED, load_yaml


def test_signed_fixture_is_the_reference_contract() -> None:
    contract = load_yaml(SIGNED)

    assert contract["id"] == "spokes-parcelly-shipping"
    assert contract["version"] == 9
    assert contract["status"] == "signed"
    assert [scenario["id"] for scenario in contract["scenarios"]] == [
        *(f"S{number}" for number in range(1, 10)),
        *(f"P{number}" for number in range(1, 5)),
        "E1",
        "E2",
    ]
    assert [decision["id"] for decision in contract["decisions"]] == [
        f"D{number}" for number in range(1, 9)
    ]


def rule_number(file_name: str) -> int:
    found = re.search(r"rule(\d)", file_name)
    assert found is not None, file_name
    return int(found.group(1))


def test_every_semantic_rule_has_its_own_fixture() -> None:
    rules = {rule_number(path.name) for path in SEMANTIC_FIXTURES}

    assert rules == {1, 2, 3, 4, 5, 6}


def test_each_broken_fixture_differs_from_the_minimal_contract_in_one_place() -> None:
    minimal_lines = MINIMAL.read_text(encoding="utf-8").splitlines()

    for path in (*SEMANTIC_FIXTURES, SCHEMA_BROKEN):
        diff = list(
            difflib.unified_diff(minimal_lines, path.read_text(encoding="utf-8").splitlines(), n=0)
        )
        added = [line for line in diff if line.startswith("+") and not line.startswith("+++")]
        removed = [line for line in diff if line.startswith("-") and not line.startswith("---")]
        assert len(added) == 1, path.name
        assert len(removed) <= 1, path.name
