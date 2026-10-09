import pytest

from glue_doctor.checks import REGISTRY
from glue_doctor.checks.collect_error import NoParams, check_collect_error
from glue_doctor.checks.observability import FLAGS, ObservabilityParams, check_observability
from glue_doctor.checks.version import VersionParams, check_version
from glue_doctor.models import CollectIssue, Severity
from tests.factories import make_job

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
    [finding] = check_version(make_job(glue_version=(5, 1)), VersionParams(warn_below="6.0"))
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


# --- observability-off ----------------------------------------------------


def test_observability_reports_all_missing_flags_in_one_finding():
    [finding] = check_observability(make_job(special_args={}), ObservabilityParams())
    assert finding.check_id == "observability-off"
    assert finding.severity is Severity.INFO
    assert finding.evidence["missing"] == list(FLAGS.values())


def test_observability_clean_when_all_enabled_case_insensitive():
    args = {flag: "TRUE" for flag in FLAGS.values()}
    assert check_observability(make_job(special_args=args), ObservabilityParams()) == []


def test_observability_false_counts_as_missing():
    args = {flag: "true" for flag in FLAGS.values()}
    args["--enable-metrics"] = "false"
    [finding] = check_observability(make_job(special_args=args), ObservabilityParams())
    assert finding.evidence["missing"] == ["--enable-metrics"]


def test_observability_require_is_configurable():
    params = ObservabilityParams(require=("metrics",))
    [finding] = check_observability(make_job(special_args={}), params)
    assert finding.evidence["missing"] == ["--enable-metrics"]


def test_observability_skips_non_spark_jobs():
    job = make_job(command="pythonshell", special_args={})
    assert check_observability(job, ObservabilityParams()) == []


def test_job_insights_not_required_before_glue_2():
    job = make_job(glue_version=(1, 0), special_args={})
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


# --- registry -------------------------------------------------------------


def test_registry_order_and_contents():
    assert list(REGISTRY) == [
        "timeout-too-generous",
        "timeout-too-tight",
        "glue-version",
        "observability-off",
        "collect-error",
    ]
    assert {s.id for s in REGISTRY.values() if s.needs_runs} == {
        "timeout-too-generous",
        "timeout-too-tight",
    }
