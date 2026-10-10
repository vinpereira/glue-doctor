import json
from typing import Any

from glue_doctor import __version__
from glue_doctor.models import Finding, Severity
from glue_doctor.runner import AuditResult

SCHEMA_VERSION = 1

_STYLES = {"error": "red", "warning": "yellow", "info": "blue"}


def _finding_dict(finding: Finding) -> dict[str, Any]:
    return {
        "check_id": finding.check_id,
        "severity": finding.severity.value,
        "job": finding.job,
        "message": finding.message,
        "remediation": finding.remediation,
        "evidence": finding.evidence,
    }


def build_report(
    result: AuditResult, *, region: str, account_id: str | None, exit_code: int
) -> dict[str, Any]:
    counts = {severity.value: 0 for severity in (Severity.ERROR, Severity.WARNING, Severity.INFO)}
    for finding in result.findings:
        counts[finding.severity.value] += 1
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": {"name": "glue-doctor", "version": __version__},
        "scope": {"region": region, "account_id": account_id},
        "summary": {
            "jobs_audited": result.jobs_audited,
            "findings": counts,
            "exit_code": exit_code,
        },
        "findings": [_finding_dict(f) for f in result.findings],
        "suppressed": [{**_finding_dict(s.finding), "reason": s.reason} for s in result.suppressed],
    }


def render_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, ensure_ascii=False)


def render_table(report: dict[str, Any]) -> None:
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text

    console = Console()
    findings = report["findings"]
    jobs = report["summary"]["jobs_audited"]
    suppressed = len(report["suppressed"])
    suffix = f" ({suppressed} suppressed)" if suppressed else ""
    if not findings:
        console.print(f"No findings in {jobs} job(s){suffix}")
        return
    table = Table(show_lines=False)
    table.add_column("Severity", no_wrap=True)
    table.add_column("Job", overflow="fold")
    table.add_column("Check", no_wrap=True)
    table.add_column("Message", overflow="fold")
    for item in findings:
        table.add_row(
            Text(item["severity"], style=_STYLES[item["severity"]]),
            Text(item["job"]),
            Text(item["check_id"]),
            Text.assemble(item["message"], ("\n" + item["remediation"], "dim")),
        )
    console.print(table)
    console.print(f"{len(findings)} finding(s) in {jobs} job(s){suffix}")
