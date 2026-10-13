import json
import os
import re
import subprocess
import sys
from importlib import resources

import jsonschema
import pytest

from glue_doctor import __version__
from glue_doctor.models import Finding, Severity
from glue_doctor.report import SCHEMA_VERSION, build_report, render_json, render_table
from glue_doctor.runner import AuditResult, SuppressedFinding


def finding(job="example-job", check="timeout-too-generous", severity=Severity.WARNING):
    return Finding(
        check_id=check,
        severity=severity,
        job=job,
        message="Timeout is 2880 min but the median successful run takes 10 min",
        remediation="Set the timeout closer to the observed runtime",
        evidence={"timeout_min": 2880, "median_min": 10.0},
    )


def result(findings=(), suppressed=(), jobs=3):
    return AuditResult(
        findings=tuple(findings),
        suppressed=tuple(suppressed),
        jobs_audited=jobs,
        warnings=(),
    )


def schema():
    text = (resources.files("glue_doctor") / "schemas" / "report-v1.json").read_text("utf-8")
    return json.loads(text)


def test_report_structure_and_summary():
    res = result(
        [finding(), finding(check="observability-off", severity=Severity.INFO)],
        [SuppressedFinding(finding(job="legacy"), "known gap")],
    )
    report = build_report(res, region="eu-central-1", account_id="123456789012", exit_code=1)
    assert report["schema_version"] == SCHEMA_VERSION == 1
    assert report["tool"] == {"name": "glue-doctor", "version": __version__}
    assert report["scope"] == {"region": "eu-central-1", "account_id": "123456789012"}
    assert report["summary"] == {
        "jobs_audited": 3,
        "findings": {"error": 0, "warning": 1, "info": 1},
        "exit_code": 1,
    }
    assert report["suppressed"][0]["reason"] == "known gap"
    assert report["suppressed"][0]["job"] == "legacy"


def test_report_validates_against_the_json_schema():
    res = result([finding()], [SuppressedFinding(finding(job="legacy"), "known gap")])
    report = build_report(res, region="eu-central-1", account_id=None, exit_code=1)
    jsonschema.validate(report, schema())
    jsonschema.validate(json.loads(render_json(report)), schema())


def test_schema_rejects_unknown_top_level_keys():
    report = build_report(result(), region="eu-central-1", account_id=None, exit_code=0)
    report["extra"] = 1
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(report, schema())


def test_json_keeps_non_ascii_and_markup_characters():
    report = build_report(
        result([finding(job="[bold]étl-ñ")]), region="eu-central-1", account_id=None, exit_code=1
    )
    text = render_json(report)
    assert "[bold]étl-ñ" in text
    assert json.loads(text)["findings"][0]["job"] == "[bold]étl-ñ"


def test_table_lists_findings_and_summary(monkeypatch, capsys):
    monkeypatch.setenv("COLUMNS", "200")
    report = build_report(
        result([finding(), finding(job="other", check="glue-version", severity=Severity.INFO)]),
        region="eu-central-1",
        account_id=None,
        exit_code=1,
    )
    render_table(report)
    out = capsys.readouterr().out
    assert "timeout-too-generous" in out and "glue-version" in out
    assert "example-job" in out and "other" in out
    assert "Set the timeout closer to the observed runtime" in out
    assert "2 finding(s) in 3 job(s)" in out
    assert "\x1b" not in out  # no color codes when not attached to a terminal


def test_table_without_findings(monkeypatch, capsys):
    monkeypatch.setenv("COLUMNS", "200")
    report = build_report(result(jobs=0), region="eu-central-1", account_id=None, exit_code=0)
    render_table(report)
    assert "No findings in 0 job(s)" in capsys.readouterr().out


def test_table_renders_markup_characters_literally(monkeypatch, capsys):
    monkeypatch.setenv("COLUMNS", "200")
    report = build_report(
        result([finding(job="[bold]étl-ñ")]), region="eu-central-1", account_id=None, exit_code=1
    )
    render_table(report)
    assert "[bold]étl-ñ" in capsys.readouterr().out


def test_table_never_wraps_the_check_id_on_a_narrow_terminal(monkeypatch, capsys):
    monkeypatch.setenv("COLUMNS", "60")
    report = build_report(
        result([finding(job="a-rather-long-job-name-for-a-narrow-terminal")]),
        region="eu-central-1",
        account_id=None,
        exit_code=1,
    )
    render_table(report)
    assert "timeout-too-generous" in capsys.readouterr().out


def test_table_mentions_suppressed_count(monkeypatch, capsys):
    monkeypatch.setenv("COLUMNS", "200")
    res = result([finding()], [SuppressedFinding(finding(job="legacy"), "known gap")])
    render_table(build_report(res, region="eu-central-1", account_id=None, exit_code=1))
    assert "1 suppressed" in capsys.readouterr().out


# --- colors on a real terminal ---------------------------------------------
# Rich only styles output when stdout is a terminal, so these run the renderer in a subprocess
# attached to a pseudo-terminal. NO_COLOR removes colors but keeps bold/dim, per the NO_COLOR spec.

_RENDER_SNIPPET = """
from glue_doctor.models import Finding, Severity
from glue_doctor.report import build_report, render_table
from glue_doctor.runner import AuditResult

finding = Finding("timeout-too-generous", Severity.WARNING, "example-job", "message", "fix", {})
result = AuditResult((finding,), (), 1, ())
render_table(build_report(result, region="eu-central-1", account_id=None, exit_code=1))
"""

_COLOR_CODE = re.compile(r"\x1b\[(?:[0-9;]*;)?(?:3[0-7]|9[0-7]|38|4[0-7]|10[0-7])(?:;[0-9;]*)?m")


def _render_on_a_terminal(extra_env):
    import pty  # Unix only, so imported lazily to keep the module importable on Windows

    master, slave = pty.openpty()
    env = {k: v for k, v in os.environ.items() if k not in ("NO_COLOR", "FORCE_COLOR")}
    env.update({"COLUMNS": "120", **extra_env})
    proc = subprocess.Popen(
        [sys.executable, "-c", _RENDER_SNIPPET], stdout=slave, stderr=slave, env=env
    )
    os.close(slave)
    chunks = []
    while True:
        try:
            data = os.read(master, 4096)
        except OSError:  # the child closed its end of the terminal
            break
        if not data:
            break
        chunks.append(data)
    proc.wait()
    os.close(master)
    return b"".join(chunks).decode("utf-8", "replace")


@pytest.mark.skipif(sys.platform == "win32", reason="needs a pseudo-terminal")
def test_table_is_colored_on_a_terminal():
    # control for the NO_COLOR test below: proves the check can detect color codes at all
    assert _COLOR_CODE.search(_render_on_a_terminal({}))


@pytest.mark.skipif(sys.platform == "win32", reason="needs a pseudo-terminal")
def test_table_has_no_color_codes_when_no_color_is_set():
    output = _render_on_a_terminal({"NO_COLOR": "1"})
    assert "timeout-too-generous" in output
    assert not _COLOR_CODE.search(output)
