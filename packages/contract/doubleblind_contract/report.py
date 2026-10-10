import json
import xml.etree.ElementTree as ElementTree
from pathlib import Path

from doubleblind_contract.runtime import RunReport, ScenarioResult

STATUS_LABELS = {"passed": "PASS", "failed": "FAIL", "error": "ERROR"}


def scenario_lines(scenario: ScenarioResult) -> list[str]:
    label = STATUS_LABELS[scenario.status]
    lines = [f"{label:<5} {scenario.id} {scenario.title} ({scenario.duration_ms} ms)"]
    lines += [f"      {failure.render()}" for failure in scenario.failures]
    return lines


def render_console(report: RunReport) -> list[str]:
    lines: list[str] = []
    for scenario in report.scenarios:
        lines += scenario_lines(scenario)
    summary = report.summary
    lines.append(
        f"{report.side}: {summary['passed']} passed, {summary['failed']} failed, "
        f"{summary['error']} error"
    )
    return lines


def write_json(report: RunReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


def junit_tree(report: RunReport) -> ElementTree.ElementTree:
    summary = report.summary
    suite = ElementTree.Element(
        "testsuite",
        {
            "name": f"{report.contract_id}.{report.side}",
            "tests": str(len(report.scenarios)),
            "failures": str(summary["failed"]),
            "errors": str(summary["error"]),
            "time": f"{sum(scenario.duration_ms for scenario in report.scenarios) / 1000:.3f}",
            "timestamp": report.started_at,
        },
    )
    for scenario in report.scenarios:
        case = ElementTree.SubElement(
            suite,
            "testcase",
            {
                "classname": f"{report.contract_id}.{report.side}",
                "name": f"{scenario.id}: {scenario.title}",
                "time": f"{scenario.duration_ms / 1000:.3f}",
            },
        )
        if scenario.status == "passed":
            continue
        tag = "failure" if scenario.status == "failed" else "error"
        details = "\n".join(failure.render() for failure in scenario.failures)
        first = scenario.failures[0].message if scenario.failures else scenario.status
        element = ElementTree.SubElement(case, tag, {"message": first})
        element.text = details
        if scenario.app_log_tail:
            ElementTree.SubElement(case, "system-out").text = scenario.app_log_tail
    return ElementTree.ElementTree(suite)


def write_junit(report: RunReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tree = junit_tree(report)
    ElementTree.indent(tree)
    tree.write(path, encoding="utf-8", xml_declaration=True)
