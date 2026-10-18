import json
import subprocess
import sys
import textwrap

import pytest
from click.testing import CliRunner

from glue_doctor import cli as cli_module
from glue_doctor.cli import cli
from glue_doctor.collector import AuditAborted, AwsContext, ReadOnlyViolation
from glue_doctor.models import (
    CatalogScope,
    CatalogSnapshot,
    CollectIssue,
    EventLogs,
    LifecycleRule,
)
from tests.factories import make_job, make_optimizer


@pytest.fixture
def fake_aws(monkeypatch):
    def install(snapshots, catalog=None):
        seen = {"catalog_called": False}

        def fake_connect(profile, region):
            seen["profile"], seen["region"] = profile, region
            return AwsContext(
                client=object(), region="eu-central-1", account_id="123456789012", s3="s3-client"
            )

        def fake_collect(client, job_filter, wanted_runs, s3_client=None):
            seen["filter"], seen["wanted"], seen["s3_client"] = job_filter, wanted_runs, s3_client
            return snapshots

        def fake_catalog(client, database_filter, min_failures, now=None):
            seen["database_filter"], seen["min_failures"] = database_filter, min_failures
            seen["catalog_called"] = True
            return catalog or CatalogSnapshot((), (), 0)

        monkeypatch.setattr(cli_module, "connect", fake_connect)
        monkeypatch.setattr(cli_module, "collect_snapshots", fake_collect)
        monkeypatch.setattr(cli_module, "collect_catalog", fake_catalog)
        return seen

    return install


@pytest.fixture(autouse=True)
def empty_cwd(tmp_path, monkeypatch):
    """Run every test in an empty directory so no stray glue-doctor.toml is discovered."""
    monkeypatch.chdir(tmp_path)


def audit(*args):
    return CliRunner().invoke(cli, ["audit", *args])


def noisy():
    return make_job(timeout=2880, runs=())  # generous warning via the absolute cap


def test_clean_audit_exits_zero(fake_aws):
    fake_aws([make_job(runs=())])
    result = audit("--format", "json")
    assert result.exit_code == 0
    report = json.loads(result.stdout)
    assert report["summary"]["findings"] == {"error": 0, "warning": 0, "info": 0}
    assert report["scope"] == {"region": "eu-central-1", "account_id": "123456789012"}


def test_account_with_zero_jobs_is_clean(fake_aws):
    fake_aws([])
    result = audit()
    assert result.exit_code == 0
    assert "No findings in 0 job(s)" in result.stdout


def test_warning_finding_fails_by_default(fake_aws):
    fake_aws([noisy()])
    result = audit("--format", "json")
    assert result.exit_code == 1
    report = json.loads(result.stdout)
    assert report["findings"][0]["check_id"] == "timeout-too-generous"
    assert report["summary"]["exit_code"] == 1


@pytest.mark.parametrize("fail_on, expected", [("error", 0), ("never", 0), ("info", 1)])
def test_fail_on_flag(fake_aws, fail_on, expected):
    fake_aws([noisy()])
    assert audit("--fail-on", fail_on).exit_code == expected


def test_info_only_findings_do_not_fail_by_default(fake_aws):
    fake_aws([make_job(runs=(), special_args={})])  # observability-off is info
    assert audit().exit_code == 0


def test_collect_error_fails_the_audit(fake_aws):
    fake_aws([make_job(collect_issues=(CollectIssue("GetJobRuns", "AccessDeniedException"),))])
    result = audit("--format", "json")
    assert result.exit_code == 1
    assert json.loads(result.stdout)["findings"][0]["check_id"] == "collect-error"


def test_audit_aborted_exits_three(monkeypatch):
    def boom(profile, region):
        raise AuditAborted("NoCredentialsError: Unable to locate credentials")

    monkeypatch.setattr(cli_module, "connect", boom)
    result = audit()
    assert result.exit_code == 3
    assert "Unable to locate credentials" in result.stderr
    assert result.stdout == ""


def test_unexpected_error_exits_three_without_leaking_details(fake_aws, monkeypatch):
    fake_aws([])

    def boom(client, job_filter, wanted_runs, s3_client=None):
        raise RuntimeError("secret-looking detail")

    monkeypatch.setattr(cli_module, "collect_snapshots", boom)
    result = audit()
    assert result.exit_code == 3
    assert "internal error: RuntimeError" in result.stderr
    assert "secret-looking detail" not in result.stderr
    assert result.stdout == ""


def test_read_only_violation_is_an_internal_error_not_a_finding(fake_aws, monkeypatch):
    fake_aws([])

    def blocked(client, job_filter, wanted_runs, s3_client=None):
        raise ReadOnlyViolation("operation UpdateJob is not allowed")

    monkeypatch.setattr(cli_module, "collect_snapshots", blocked)
    result = audit()
    assert result.exit_code == 3
    assert "ReadOnlyViolation" in result.stderr


def test_empty_job_glob_is_a_usage_error(fake_aws):
    fake_aws([])
    result = audit("--job", "")
    assert result.exit_code == 2
    assert "--job" in result.stderr


def test_no_matching_jobs_warns_on_stderr_but_stays_clean(fake_aws):
    fake_aws([])
    result = audit("--format", "json")
    assert result.exit_code == 0
    assert "no jobs" in result.stderr.lower()
    json.loads(result.stdout)


def test_non_utf8_config_exits_two(fake_aws, tmp_path):
    fake_aws([])
    (tmp_path / "glue-doctor.toml").write_bytes(b"\xff\xfe\x00")
    result = audit()
    assert result.exit_code == 2
    assert "cannot read" in result.stderr


def test_invalid_config_exits_two(fake_aws, tmp_path):
    fake_aws([])
    path = tmp_path / "bad.toml"
    path.write_text("bogus = 1\n")
    result = audit("--config", str(path))
    assert result.exit_code == 2
    assert "bogus" in result.stderr


def test_missing_explicit_config_exits_two(fake_aws, tmp_path):
    fake_aws([])
    result = audit("--config", str(tmp_path / "nope.toml"))
    assert result.exit_code == 2
    assert "not found" in result.stderr


def test_config_file_in_working_directory_is_discovered(fake_aws, tmp_path):
    fake_aws([noisy()])
    (tmp_path / "glue-doctor.toml").write_text('fail_on = "never"\n')
    assert audit().exit_code == 0


def test_flags_override_the_config_file(fake_aws, tmp_path):
    seen = fake_aws([])
    (tmp_path / "glue-doctor.toml").write_text('region = "eu-west-1"\nprofile = "from-file"\n')
    audit("--region", "eu-north-1")
    assert seen["region"] == "eu-north-1" and seen["profile"] == "from-file"


def test_job_flag_overrides_include_and_wanted_runs_follow_config(fake_aws):
    seen = fake_aws([])
    audit("--job", "etl-*")
    assert seen["filter"]("etl-a") and not seen["filter"]("other")
    assert seen["wanted"] == 20


def test_warnings_go_to_stderr_and_stdout_stays_valid_json(fake_aws, tmp_path):
    fake_aws([noisy()])
    (tmp_path / "glue-doctor.toml").write_text(
        '[[suppress]]\ncheck = "timeout-too-generous"\nreason = "x"\nuntil = 2020-01-01\n'
    )
    result = audit("--format", "json")
    assert "expired on 2020-01-01" in result.stderr
    json.loads(result.stdout)


def test_table_output(fake_aws):
    fake_aws([noisy()])
    result = CliRunner().invoke(cli, ["audit"], env={"COLUMNS": "200"})
    assert "timeout-too-generous" in result.stdout and "example-job" in result.stdout


def test_job_option_help_says_the_catalog_scan_is_not_filtered():
    result = CliRunner().invoke(cli, ["audit", "--help"])
    assert result.exit_code == 0
    assert "catalog scan is not affected" in " ".join(result.output.split())


def test_help_lists_every_check_without_credentials():
    result = CliRunner().invoke(cli, ["audit", "--help"])
    assert result.exit_code == 0
    for check_id in (
        "timeout-too-generous",
        "timeout-too-tight",
        "glue-version",
        "observability-off",
        "spark-ui-no-lifecycle",
        "iceberg-optimizer-suspended",
        "collect-error",
    ):
        assert check_id in result.stdout


def test_json_format_never_imports_rich():
    code = textwrap.dedent(
        """
        import sys
        from unittest import mock
        from click.testing import CliRunner
        from glue_doctor import cli as cli_module
        from glue_doctor.collector import AwsContext
        from glue_doctor.models import CatalogSnapshot

        ctx = AwsContext(client=object(), region="eu-central-1", account_id=None)
        with mock.patch.object(cli_module, "connect", return_value=ctx), \\
             mock.patch.object(cli_module, "collect_snapshots", return_value=[]), \\
             mock.patch.object(cli_module, "collect_catalog", return_value=CatalogSnapshot((), (), 0)):
            result = CliRunner().invoke(cli_module.cli, ["audit", "--format", "json"])
        assert result.exit_code == 0, result.output
        assert "rich" not in sys.modules, "rich was imported on the JSON path"
        """
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def spark_job(rules, issues=()):
    logs = EventLogs("example-logs", "spark-ui/", rules)
    return make_job(runs=(), event_logs=logs, collect_issues=issues)


def test_uncovered_event_logs_is_a_finding(fake_aws):
    fake_aws([spark_job(())])
    result = audit("--format", "json")
    assert result.exit_code == 1
    [finding] = json.loads(result.stdout)["findings"]
    assert finding["check_id"] == "spark-ui-no-lifecycle"
    assert finding["evidence"]["bucket"] == "example-logs"


def test_covered_event_logs_is_clean(fake_aws):
    fake_aws([spark_job((LifecycleRule(True, "", True, False),))])
    assert audit().exit_code == 0


def test_no_s3_permission_at_all_gives_a_collect_error_per_job(fake_aws):
    denial = (CollectIssue("GetBucketLifecycleConfiguration", "AccessDenied"),)
    jobs = [
        make_job(
            name=f"example-job-{n}",
            runs=(),
            event_logs=EventLogs("example-logs", "spark-ui/", None),
            collect_issues=denial,
        )
        for n in (1, 2)
    ]
    fake_aws(jobs)
    result = audit("--format", "json")
    assert result.exit_code == 1
    findings = json.loads(result.stdout)["findings"]
    assert [f["check_id"] for f in findings] == ["collect-error", "collect-error"]
    assert all("s3:GetLifecycleConfiguration" in f["remediation"] for f in findings)


def test_s3_client_is_only_passed_when_the_check_is_enabled(fake_aws, tmp_path):
    seen = fake_aws([])
    audit()
    assert seen["s3_client"] == "s3-client"
    (tmp_path / "glue-doctor.toml").write_text("[checks.spark-ui-no-lifecycle]\nenabled = false\n")
    audit()
    assert seen["s3_client"] is None


def test_a_failing_optimizer_is_a_finding_with_a_table_resource(fake_aws):
    broken = make_optimizer("compaction", last_event="failed", recent=("failed", "failed"))
    fake_aws([], catalog=CatalogSnapshot((broken,), (), 3))
    result = audit("--format", "json")
    assert result.exit_code == 1
    report = json.loads(result.stdout)
    [finding] = report["findings"]
    assert (finding["resource_type"], finding["resource"]) == ("table", "example_db.example_table")
    assert report["summary"]["tables_audited"] == 3 and report["schema_version"] == 2


def test_healthy_catalog_is_clean(fake_aws):
    healthy = make_optimizer("retention", age_days=1)
    fake_aws([], catalog=CatalogSnapshot((healthy,), (), 1))
    assert audit().exit_code == 0


def test_catalog_denial_is_a_catalog_collect_error(fake_aws):
    issue = CollectIssue("GetDatabases", "AccessDeniedException", "catalog")
    catalog = CatalogSnapshot((), (CatalogScope("catalog", (issue,)),), 0)
    fake_aws([make_job(runs=())], catalog=catalog)
    result = audit("--format", "json")
    assert result.exit_code == 1
    [finding] = json.loads(result.stdout)["findings"]
    assert (finding["check_id"], finding["resource_type"]) == ("collect-error", "catalog")
    assert "glue:GetDatabases" in finding["remediation"]
    assert "iceberg-optimizer-suspended" in finding["remediation"]


def test_one_database_failing_does_not_hide_the_rest(fake_aws):
    issue = CollectIssue("GetTables", "AccessDeniedException", "example_db")
    broken = make_optimizer(
        "compaction", database="other_db", last_event="failed", recent=("failed", "failed")
    )
    fake_aws([], catalog=CatalogSnapshot((broken,), (CatalogScope("example_db", (issue,)),), 2))
    findings = json.loads(audit("--format", "json").stdout)["findings"]
    ids = sorted(f["check_id"] for f in findings)
    assert ids == ["collect-error", "iceberg-optimizer-suspended"]


def test_catalog_is_only_scanned_when_the_check_is_enabled(fake_aws, tmp_path):
    seen = fake_aws([])
    audit()
    assert seen["catalog_called"] is True and seen["min_failures"] == 2
    seen["catalog_called"] = False
    (tmp_path / "glue-doctor.toml").write_text(
        "[checks.iceberg-optimizer-suspended]\nenabled = false\n"
    )
    audit()
    assert seen["catalog_called"] is False


def test_no_catalog_flag_skips_the_catalog_scan_and_keeps_job_findings(fake_aws):
    seen = fake_aws([noisy()])
    result = audit("--no-catalog", "--format", "json")
    assert result.exit_code == 1  # the job finding still counts
    assert seen["catalog_called"] is False
    report = json.loads(result.stdout)
    assert report["summary"]["tables_audited"] == 0


def test_catalog_is_scanned_without_the_flag(fake_aws):
    seen = fake_aws([make_job(runs=())])
    assert audit().exit_code == 0
    assert seen["catalog_called"] is True


def test_help_documents_no_catalog():
    result = CliRunner().invoke(cli, ["audit", "--help"])
    assert result.exit_code == 0
    assert "--no-catalog" in result.output


def test_catalog_section_filters_databases_and_min_failures_is_passed(fake_aws, tmp_path):
    seen = fake_aws([])
    (tmp_path / "glue-doctor.toml").write_text(
        '[catalog]\ninclude = ["prod_*"]\n[checks.iceberg-optimizer-suspended]\nmin_failures = 3\n'
    )
    audit()
    assert seen["database_filter"]("prod_sales") and not seen["database_filter"]("dev_sales")
    assert seen["min_failures"] == 3
