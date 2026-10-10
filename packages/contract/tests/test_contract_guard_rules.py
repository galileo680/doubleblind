import pytest

from doubleblind_contract.guard_rules import (
    SyntheticAllowlist,
    check_message,
    check_test_run,
    check_text,
    check_turn,
    code_line_count,
    normalize_phone,
    redact,
    shannon_entropy,
)

ALLOWLIST = SyntheticAllowlist.build(["600 100 200", "+48500200300"], ["jan.kowalski@example.com"])


def reasons(text: str) -> list[str]:
    return check_text(text, ("message",), ALLOWLIST).reasons


def test_plain_interface_language_passes() -> None:
    verdict = check_message(
        {
            "message": "Waga w gramach jako liczba całkowita.\nreturn value is the shipment id",
            "phone": "+48600100200",
            "email": "jan.kowalski@example.com",
        },
        ALLOWLIST,
    )

    assert not verdict.blocked
    assert verdict.code_lines == 1


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("Here it is:\n```python\nx = 1\n```", "code blocks are not allowed"),
        ("def create(order):\n    return order\nimport os", "looks like source code"),
        ("const total = items.reduce((a, b) => a + b);\nlet x = 1;", "looks like source code"),
        ("-----BEGIN RSA PRIVATE KEY-----", "contains a secret"),
        ("token sk-abcdefghijklmnopqrstuvwx", "contains a secret"),
        ("AKIAABCDEFGHIJKLMNOP", "contains a secret"),
        ("set NEBIUS_API_KEY first", "contains a secret"),
        ("key Zx9qL2mN8vB4cT7yR1wK6pE3sD5fG0hJ", "high-entropy token"),
        ("write to anna.nowak@real-company.pl", "personal e-mail"),
        ("call 512 345 678 tomorrow", "phone number outside the synthetic data"),
        ("Traceback (most recent call last):\n  boom", "stack trace"),
        ("at handler (/srv/app/src/index.js:10:5)", "stack trace"),
    ],
)
def test_leaks_are_blocked(text: str, reason: str) -> None:
    assert any(reason in item for item in reasons(text)), reasons(text)


def test_paths_and_identifiers_are_not_mistaken_for_secrets() -> None:
    assert reasons("/__sandbox/shipments/by-reference/{{args.reference}}/advance") == []
    assert reasons("shipment_status_to_order_status_mapping_for_partners") == []


def test_code_is_allowed_in_contract_sections_and_bindings() -> None:
    fenced = "```yaml\ntype: object\n```"
    turn = {
        "message": "Schemat poniżej.",
        "patches": [
            {"op": "add", "path": "/schemas/Example", "value": {"description": fenced}},
            {"op": "add", "path": "/decisions/-", "value": {"text": fenced}},
        ],
    }

    verdict = check_turn(turn, ALLOWLIST)

    assert verdict.reasons == ["patches[1].value.text: code blocks are not allowed"]
    assert not check_test_run({"binding_yaml": fenced}, ALLOWLIST).blocked
    assert check_test_run({"results": [{"sanitized_reason": fenced}]}, ALLOWLIST).blocked


def test_repository_paths_are_redacted_not_blocked() -> None:
    verdict = check_text(
        "The rule lives in src/rules/weight.js",
        ("message",),
        ALLOWLIST,
        repo_paths=["src/rules/weight.js"],
    )

    assert not verdict.blocked
    assert redact("see src/rules/weight.js", verdict.redactions) == "see [internal]"


def test_helpers() -> None:
    assert normalize_phone("0048 600-100-200") == "600100200"
    assert normalize_phone("+48600100200") == "600100200"
    assert code_line_count("import os\nplain text\nreturn x") == 2
    assert shannon_entropy("aaaa") == 0
