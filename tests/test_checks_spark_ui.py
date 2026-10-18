import pytest

from glue_doctor.checks.collect_error import NoParams
from glue_doctor.checks.spark_ui import check_spark_ui
from glue_doctor.models import EventLogs, LifecycleRule, Severity
from tests.factories import make_job


def rule(prefix="", enabled=True, expires=True, scoped=False):
    return LifecycleRule(enabled=enabled, prefix=prefix, expires=expires, scoped=scoped)


def job_with(rules, prefix="spark-ui/", command="glueetl", bucket="example-logs"):
    return make_job(command=command, event_logs=EventLogs(bucket, prefix, rules))


def run(job):
    return check_spark_ui(job, NoParams())


@pytest.mark.parametrize(
    "prefix, rules",
    [
        ("spark-ui/", (rule(""),)),  # whole-bucket rule
        ("spark-ui/", (rule("spark-ui/"),)),  # exact prefix
        ("spark-ui/", (rule("spark-ui"),)),  # rule prefix without the trailing slash
        ("spark-ui/", (rule("spark"),)),  # shorter prefix
        ("a/b/c/", (rule("a/"),)),
        ("", (rule(""),)),  # logs at the bucket root
        ("spark-ui/", (rule("spark-ui/old/"), rule("spark-ui/"))),  # one covering rule is enough
        ("spark-ui/", (rule("other/", enabled=False), rule(""))),
    ],
)
def test_covered_logs_have_no_finding(prefix, rules):
    assert run(job_with(rules, prefix=prefix)) == []


def test_no_lifecycle_configuration():
    [finding] = run(job_with(()))
    assert finding.check_id == "spark-ui-no-lifecycle"
    assert finding.severity is Severity.WARNING
    assert finding.resource == "example-job"
    assert finding.resource_type == "job"
    assert finding.evidence == {
        "bucket": "example-logs",
        "prefix": "spark-ui/",
        "reason": "no-lifecycle-configuration",
        "partial_rules": 0,
    }
    assert "s3://example-logs/spark-ui/" in finding.message
    assert "expire" in finding.remediation.lower()


@pytest.mark.parametrize(
    "prefix, rules, partial",
    [
        ("spark-ui/", (rule("spark-ui/old/"),), 1),  # narrower than the logs
        ("spark-ui/", (rule("", scoped=True),), 1),  # tag or size filter on the whole bucket
        ("spark-ui/", (rule("spark-ui/", scoped=True),), 1),
        ("spark-ui/", (rule("spark-ui/old/", scoped=True),), 1),
        ("", (rule("logs/"),), 1),  # root logs, rule only covers a sub-prefix
        ("spark-ui/", (rule("", enabled=False),), 0),  # disabled
        ("spark-ui/", (rule("", expires=False),), 0),  # transitions only
        ("spark-ui/", (rule("other/"),), 0),  # unrelated prefix
        ("spark-ui/", (rule("spark-ui/old/"), rule("", scoped=True)), 2),
    ],
)
def test_logs_not_covered_by_any_rule(prefix, rules, partial):
    [finding] = run(job_with(rules, prefix=prefix))
    assert finding.evidence["reason"] == "no-covering-rule"
    assert finding.evidence["partial_rules"] == partial


def test_message_for_the_bucket_root():
    [finding] = run(job_with((), prefix=""))
    assert "s3://example-logs/" in finding.message


def test_unreadable_bucket_is_left_to_collect_error():
    assert run(job_with(None)) == []


def test_job_without_event_logs():
    assert run(make_job()) == []


def test_python_shell_jobs_are_skipped():
    assert run(job_with((), command="pythonshell")) == []
