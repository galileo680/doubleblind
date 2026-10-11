from html import escape
from typing import Any

SIDES = (("consumer", "Consumer"), ("provider", "Provider"), ("e2e", "End-to-end"))
STYLE = """
body { font-family: system-ui, sans-serif; margin: 2rem auto; max-width: 52rem; padding: 0 1rem;
       color: #1d2433; background: #fff; }
h1 { margin-bottom: 0.2rem; }
.verdict { font-size: 1.3rem; font-weight: 600; }
.ok { color: #1a7f37; } .bad { color: #b42318; }
table { border-collapse: collapse; width: 100%; margin: 0.6rem 0 1.4rem; }
th, td { text-align: left; padding: 0.35rem 0.6rem; border-bottom: 1px solid #e3e6ec; }
code { font-size: 0.85rem; word-break: break-all; }
"""


def usd(value: float) -> str:
    return f"${value:.4f}"


def row(*cells: Any) -> str:
    return "<tr>" + "".join(f"<td>{escape(str(cell))}</td>" for cell in cells) + "</tr>"


def scenario_rows(certificate: dict[str, Any]) -> list[str]:
    rows = []
    for side, label in SIDES:
        run = certificate["results"].get(side)
        if run is None:
            rows.append(row(label, "-", "not run"))
            continue
        results = run["results"]
        items = results["scenarios"] if isinstance(results, dict) else results
        for item in items or []:
            scenario = item.get("id") or item.get("scenario_id")
            rows.append(row(label, scenario, item["status"]))
    return rows


def render_certificate(certificate: dict[str, Any]) -> str:
    metrics, audit = certificate["metrics"], certificate["audit"]
    gateway, costs = certificate["gateway"], certificate["costs"]
    certified = certificate["certified"]
    verdict = "CERTIFIED" if certified else f"not certified (state {certificate['state']})"
    cost_rows = [row("model", name, usd(cost)) for name, cost in sorted(costs["models"].items())]
    cost_rows += [
        row("sandbox", party, usd(cost)) for party, cost in sorted(costs["sandboxes"].items())
    ]
    cost_rows.append(row("total", "", usd(costs["total_usd"])))
    facts = [
        row("Session", certificate["session_id"]),
        row("Signed contract version", certificate["signed_contract_version"]),
        row("Contract SHA-256", certificate["contract_sha256"]),
        row("Code lines shared", metrics["code_lines_shared"]),
        row(
            "Egress blocked / redacted",
            f"{metrics['blocked_count']} / {metrics['redacted_count']}",
        ),
        row(
            "Gateway requests / refused / schema violations",
            f"{gateway['requests']} / {gateway['refused']} / {gateway['schema_violations']}",
        ),
        row("Audit chain", "verified" if audit["ok"] else "BROKEN"),
        row("Last event hash", audit["last_hash"]),
        row("Events", audit["events"]),
    ]
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DoubleBlind certificate</title><style>{STYLE}</style></head>
<body>
<h1>DoubleBlind certificate</h1>
<p class="verdict {"ok" if certified else "bad"}">{escape(verdict)}</p>
<table>{"".join(facts)}</table>
<h2>Scenarios</h2>
<table><tr><th>Side</th><th>Scenario</th><th>Status</th></tr>{"".join(scenario_rows(certificate))}</table>
<h2>Session cost</h2>
<table><tr><th>Kind</th><th>Name</th><th>USD</th></tr>{"".join(cost_rows)}</table>
</body></html>
"""
