import pytest

from glue_doctor.checks import REGISTRY
from glue_doctor.checks.collect_error import NoParams, check_collect_error
from glue_doctor.checks.observability import FLAGS, ObservabilityParams, check_observability
from glue_doctor.checks.version import VersionParams, check_version
from glue_doctor.models import CatalogScope, CollectIssue, Severity
from tests.factories import make_job, make_optimizer

# --- glue-version ---------------------------------------------------------


@pytest.mark.parametrize(
    "version, severity",
    [
        ((3, 0), Severity.WARNING),
        ((4, 0), Severity.WARNING),
        ((5, 0), Severity.INFO),
    ],
)
def test_old_versions_are_flagged(version, severity):
    [finding] = check_version(make_job(glue_version=version), VersionParams())
    assert finding.check_id == "glue-version"
    assert finding.severity is severity
    assert finding.evidence["glue_version"] == f"{version[0]}.{version[1]}"


@pytest.mark.parametrize("version", [(5, 1), (6, 0), (6, 1), (7, 0), (9, 9), None])
def test_current_and_future_versions_are_clean(version):
    assert check_version(make_job(glue_version=version), VersionParams()) == []


def test_message_mentions_runtime_for_known_versions():
    [finding] = check_version(make_job(glue_version=(4, 0)), VersionParams())
    assert "Spark 3.3.0" in finding.message and "Python 3.10" in finding.message


def test_version_thresholds_are_configurable():
    strict = VersionParams(warn_below="6.0", info_below="6.0")
    [finding] = check_version(make_job(glue_version=(5, 1)), strict)
    assert finding.severity is Severity.WARNING
    assert check_version(make_job(glue_version=(5, 0)), VersionParams(info_below="5.0")) == []


def test_streaming_jobs_are_checked_but_python_shell_is_not():
    streaming = make_job(command="gluestreaming", glue_version=(4, 0))
    assert len(check_version(streaming, VersionParams())) == 1
    shell = make_job(command="pythonshell", glue_version=(3, 0))
    assert check_version(shell, VersionParams()) == []


def test_version_params_are_validated():
    with pytest.raises(ValueError, match="warn_below"):
        VersionParams(warn_below="abc")
    with pytest.raises(ValueError, match="info_below"):
        VersionParams(info_below="")


def test_warn_below_must_not_exceed_info_below():
    with pytest.raises(ValueError, match="warn_below"):
        VersionParams(warn_below="5.1", info_below="4.0")
    VersionParams(warn_below="5.0", info_below="5.0")  # equal thresholds are allowed


# --- observability-off ----------------------------------------------------
# Semantics follow the Glue documentation: --enable-metrics is a presence-based flag,
# --enable-job-insights is on by default, and continuous logging is deprecated in Glue 5.0+
# (every job has real-time logging there).


def test_observability_reports_all_missing_flags_in_one_finding():
    job = make_job(glue_version=(4, 0), special_args={"--enable-job-insights": "false"})
    [finding] = check_observability(job, ObservabilityParams())
    assert finding.check_id == "observability-off"
    assert finding.severity is Severity.INFO
    assert finding.evidence["missing"] == list(FLAGS.values())


def test_observability_clean_when_all_enabled_case_insensitive():
    args = {flag: "TRUE" for flag in FLAGS.values()}
    assert (
        check_observability(make_job(glue_version=(4, 0), special_args=args), ObservabilityParams())
        == []
    )


@pytest.mark.parametrize("value", ["", "true", "anything"])
def test_metrics_is_presence_based(value):
    job = make_job(special_args={"--enable-metrics": value})
    assert check_observability(job, ObservabilityParams()) == []


def test_metrics_absent_is_reported():
    [finding] = check_observability(make_job(special_args={}), ObservabilityParams())
    assert finding.evidence["missing"] == ["--enable-metrics"]


def test_job_insights_is_on_by_default():
    job = make_job(special_args={"--enable-metrics": ""})
    assert check_observability(job, ObservabilityParams()) == []


@pytest.mark.parametrize("value", ["false", "FALSE", " False "])
def test_job_insights_explicitly_false_is_reported(value):
    job = make_job(special_args={"--enable-metrics": "", "--enable-job-insights": value})
    [finding] = check_observability(job, ObservabilityParams())
    assert finding.evidence["missing"] == ["--enable-job-insights"]


@pytest.mark.parametrize("version", [(5, 0), (5, 1), (6, 0), (7, 0), None])
def test_continuous_logging_not_required_from_glue_5_or_when_unknown(version):
    job = make_job(glue_version=version, special_args={"--enable-metrics": ""})
    assert check_observability(job, ObservabilityParams()) == []


@pytest.mark.parametrize("version", [(3, 0), (4, 0)])
@pytest.mark.parametrize("args", [{}, {"--enable-continuous-cloudwatch-log": "false"}])
def test_continuous_logging_required_before_glue_5(version, args):
    job = make_job(glue_version=version, special_args={"--enable-metrics": "", **args})
    [finding] = check_observability(job, ObservabilityParams())
    assert finding.evidence["missing"] == ["--enable-continuous-cloudwatch-log"]


def test_observability_require_is_configurable():
    params = ObservabilityParams(require=("metrics",))
    job = make_job(glue_version=(4, 0), special_args={})
    [finding] = check_observability(job, params)
    assert finding.evidence["missing"] == ["--enable-metrics"]


def test_observability_skips_non_spark_jobs():
    job = make_job(command="pythonshell", special_args={})
    assert check_observability(job, ObservabilityParams()) == []


def test_job_insights_not_checked_before_glue_2():
    job = make_job(
        glue_version=(1, 0),
        special_args={"--enable-metrics": "", "--enable-job-insights": "false"},
    )
    [finding] = check_observability(job, ObservabilityParams())
    assert "--enable-job-insights" not in finding.evidence["missing"]


def test_observability_params_are_validated():
    with pytest.raises(ValueError, match="bogus"):
        ObservabilityParams(require=("bogus",))


# --- collect-error --------------------------------------------------------


def test_collect_error_one_finding_per_issue():
    job = make_job(collect_issues=(CollectIssue("GetJobRuns", "AccessDeniedException"),))
    [finding] = check_collect_error(job, NoParams())
    assert finding.check_id == "collect-error"
    assert finding.severity is Severity.WARNING
    assert finding.evidence == {"operation": "GetJobRuns", "error_code": "AccessDeniedException"}


def test_collect_error_clean_job():
    assert check_collect_error(make_job(), NoParams()) == []


def test_collect_error_tells_how_to_fix_an_s3_denial():
    issue = CollectIssue("GetBucketLifecycleConfiguration", "AccessDenied")
    [finding] = check_collect_error(make_job(collect_issues=(issue,)), NoParams())
    assert "s3:GetLifecycleConfiguration" in finding.remediation
    assert "spark-ui-no-lifecycle" in finding.remediation


def test_collect_error_keeps_the_generic_hint_for_other_operations():
    issue = CollectIssue("GetJobRuns", "AccessDeniedException")
    [finding] = check_collect_error(make_job(collect_issues=(issue,)), NoParams())
    assert "Grant the missing read permission" in finding.remediation


def test_collect_error_for_a_table_snapshot():
    issue = CollectIssue(
        "BatchGetTableOptimizer", "AccessDeniedException", "example_db.example_table"
    )
    [finding] = check_collect_error(make_optimizer(collect_issues=(issue,)), NoParams())
    assert (finding.resource_type, finding.resource) == ("table", "example_db.example_table")
    assert finding.evidence["target"] == "example_db.example_table"
    assert "glue:BatchGetTableOptimizer" in finding.remediation


def test_collect_error_for_a_catalog_scope():
    issue = CollectIssue("GetTables", "AccessDeniedException", "example_db")
    [finding] = check_collect_error(CatalogScope("example_db", (issue,)), NoParams())
    assert (finding.resource_type, finding.resource) == ("catalog", "example_db")
    assert "glue:GetTables" in finding.remediation
    assert "iceberg-optimizer-suspended" in finding.remediation


def test_collect_error_s3_hint_only_for_denials():
    denied = CollectIssue("GetBucketLifecycleConfiguration", "AccessDenied", "example-logs")
    [with_hint] = check_collect_error(make_job(collect_issues=(denied,)), NoParams())
    assert "s3:GetLifecycleConfiguration" in with_hint.remediation
    assert with_hint.evidence["target"] == "example-logs"
    for code in ("NoSuchBucket", "BucketRegionUnresolved", "EndpointConnectionError"):
        issue = CollectIssue("GetBucketLifecycleConfiguration", code, "example-logs")
        [finding] = check_collect_error(make_job(collect_issues=(issue,)), NoParams())
        assert "s3:GetLifecycleConfiguration" not in finding.remediation
        assert "Grant the missing read permission or retry" in finding.remediation


def test_collect_error_without_a_target_has_no_target_key():
    issue = CollectIssue("GetJobRuns", "AccessDeniedException")
    [finding] = check_collect_error(make_job(collect_issues=(issue,)), NoParams())
    assert "target" not in finding.evidence


# --- registry -------------------------------------------------------------


def test_registry_order_and_contents():
    assert list(REGISTRY) == [
        "timeout-too-generous",
        "timeout-too-tight",
        "glue-version",
        "observability-off",
        "spark-ui-no-lifecycle",
        "iceberg-optimizer-suspended",
        "collect-error",
    ]
    assert {s.id for s in REGISTRY.values() if s.needs_runs} == {
        "timeout-too-generous",
        "timeout-too-tight",
    }
    assert {s.id for s in REGISTRY.values() if s.needs_event_log_lifecycle} == {
        "spark-ui-no-lifecycle"
    }
    assert {s.id for s in REGISTRY.values() if s.needs_catalog} == {"iceberg-optimizer-suspended"}
    subjects = {s.id: s.subject for s in REGISTRY.values()}
    assert subjects["iceberg-optimizer-suspended"] == "optimizer"
    assert subjects["collect-error"] == "any"
    job_subjects = {
        v for k, v in subjects.items() if k not in ("iceberg-optimizer-suspended", "collect-error")
    }
    assert job_subjects == {"job"}
