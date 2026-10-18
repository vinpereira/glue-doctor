from datetime import datetime

import boto3
import pytest
from botocore.awsrequest import AWSResponse
from botocore.stub import Stubber

from glue_doctor import collector
from glue_doctor.collector import (
    S3_ALLOWED_OPERATIONS,
    ReadOnlyViolation,
    _fetch_lifecycle,
    collect_snapshots,
    event_log_target,
    install_read_only_guard,
    parse_lifecycle_rules,
)
from glue_doctor.models import CollectIssue, EventLogs, LifecycleRule
from tests.factories import make_job

# --- which jobs have event logs, and where ---------------------------------------------------


def spark_job(path="s3://example-logs/spark-ui/", ui="true", command="glueetl"):
    args = {"--enable-spark-ui": ui, "--spark-event-logs-path": path}
    return make_job(command=command, special_args=args)


@pytest.mark.parametrize(
    "path, expected",
    [
        ("s3://example-logs/spark-ui/", ("example-logs", "spark-ui/")),
        ("s3://example-logs/spark-ui", ("example-logs", "spark-ui/")),
        ("s3://example-logs/a/b/c", ("example-logs", "a/b/c/")),
        ("s3://example-logs", ("example-logs", "")),
        ("s3://example-logs/", ("example-logs", "")),
        ("  s3://example-logs/spark-ui/  ", ("example-logs", "spark-ui/")),
        ("s3://", None),
        ("s3:///spark-ui", None),
        ("s3a://example-logs/spark-ui", None),
        ("https://example.com/spark-ui", None),
        ("example-logs/spark-ui", None),
        ("", None),
    ],
)
def test_event_log_target_parses_the_path(path, expected):
    assert event_log_target(spark_job(path=path)) == expected


@pytest.mark.parametrize("ui", ["true", "TRUE", " True "])
def test_spark_ui_flag_is_case_insensitive(ui):
    assert event_log_target(spark_job(ui=ui)) == ("example-logs", "spark-ui/")


@pytest.mark.parametrize("ui", ["false", "", "yes"])
def test_spark_ui_must_be_true(ui):
    assert event_log_target(spark_job(ui=ui)) is None


def test_spark_ui_flag_absent():
    job = make_job(special_args={"--spark-event-logs-path": "s3://example-logs/x"})
    assert event_log_target(job) is None


def test_path_absent():
    assert event_log_target(make_job(special_args={"--enable-spark-ui": "true"})) is None


def test_python_shell_jobs_have_no_event_logs():
    assert event_log_target(spark_job(command="pythonshell")) is None


def test_streaming_jobs_are_eligible():
    assert event_log_target(spark_job(command="gluestreaming")) == ("example-logs", "spark-ui/")


# --- reading the lifecycle configuration ------------------------------------------------------


def rule(**changes):
    base = {"ID": "r", "Status": "Enabled", "Expiration": {"Days": 30}}
    return {**base, **changes}


@pytest.mark.parametrize(
    "raw, expected",
    [
        (rule(Filter={"Prefix": "logs/"}), LifecycleRule(True, "logs/", True, False)),
        (rule(Prefix="logs/"), LifecycleRule(True, "logs/", True, False)),  # legacy form
        (rule(Filter={}), LifecycleRule(True, "", True, False)),
        (rule(Filter={"Prefix": ""}), LifecycleRule(True, "", True, False)),
        (
            rule(Filter={"And": {"Prefix": "logs/", "Tags": [{"Key": "k", "Value": "v"}]}}),
            LifecycleRule(True, "logs/", True, True),
        ),
        (
            rule(Filter={"Tag": {"Key": "k", "Value": "v"}}),
            LifecycleRule(True, "", True, True),
        ),
        (
            rule(Filter={"ObjectSizeGreaterThan": 1024}),
            LifecycleRule(True, "", True, True),
        ),
        (
            rule(Filter={"And": {"Prefix": "logs/", "ObjectSizeLessThan": 99}}),
            LifecycleRule(True, "logs/", True, True),
        ),
        (
            rule(Filter={"Prefix": "logs/"}, Expiration={"Date": datetime(2030, 1, 1)}),
            LifecycleRule(True, "logs/", True, False),
        ),
        (
            rule(Filter={"Prefix": "logs/"}, Expiration={"ExpiredObjectDeleteMarker": True}),
            LifecycleRule(True, "logs/", False, False),
        ),
        (
            {
                "ID": "r",
                "Status": "Enabled",
                "Filter": {"Prefix": "logs/"},
                "Transitions": [{"Days": 30, "StorageClass": "GLACIER"}],
            },
            LifecycleRule(True, "logs/", False, False),
        ),
        (
            {
                "ID": "r",
                "Status": "Enabled",
                "Filter": {"Prefix": "logs/"},
                "NoncurrentVersionExpiration": {"NoncurrentDays": 7},
            },
            LifecycleRule(True, "logs/", False, False),
        ),
        (
            rule(Status="Disabled", Filter={"Prefix": "logs/"}),
            LifecycleRule(False, "logs/", True, False),
        ),
    ],
)
def test_parse_lifecycle_rules(raw, expected):
    assert parse_lifecycle_rules([raw]) == (expected,)


def test_parse_lifecycle_rules_keeps_order_and_handles_none():
    first, second = rule(Filter={"Prefix": "a/"}), rule(Filter={"Prefix": "b/"})
    assert [r.prefix for r in parse_lifecycle_rules([first, second])] == ["a/", "b/"]
    assert parse_lifecycle_rules([]) == ()


# --- collecting it ----------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def single_worker(monkeypatch):
    # Stubber queues responses in call order, so keep collection sequential in tests.
    monkeypatch.setattr(collector, "MAX_WORKERS", 1)


@pytest.fixture
def clients():
    keys = {"aws_access_key_id": "t", "aws_secret_access_key": "t"}
    glue = boto3.client("glue", region_name="eu-central-1", **keys)
    s3 = boto3.client("s3", region_name="eu-central-1", **keys)
    install_read_only_guard(glue)
    install_read_only_guard(s3, service="s3", allowed=S3_ALLOWED_OPERATIONS)
    with Stubber(glue) as glue_stub, Stubber(s3) as s3_stub:
        yield glue, glue_stub, s3, s3_stub
        glue_stub.assert_no_pending_responses()
        s3_stub.assert_no_pending_responses()


def raw_job(name, path=None, ui="true", command="glueetl"):
    arguments = {}
    if ui is not None:
        arguments["--enable-spark-ui"] = ui
    if path is not None:
        arguments["--spark-event-logs-path"] = path
    return {
        "Name": name,
        "Command": {"Name": command},
        "GlueVersion": "5.1",
        "Timeout": 60,
        "DefaultArguments": arguments,
    }


def lifecycle_response(*rules):
    return {"Rules": list(rules)}


def expire_rule(prefix="spark-ui/"):
    return {
        "ID": "expire",
        "Status": "Enabled",
        "Filter": {"Prefix": prefix},
        "Expiration": {"Days": 30},
    }


def everything(_name):
    return True


def test_one_call_per_bucket_even_with_many_jobs(clients):
    glue, glue_stub, s3, s3_stub = clients
    jobs = [
        raw_job("a", "s3://example-logs/spark-ui/"),
        raw_job("b", "s3://example-logs/spark-ui/"),
        raw_job("c", "s3://example-logs/other/"),
        raw_job("d", "s3://other-logs/ui/"),
    ]
    glue_stub.add_response("get_jobs", {"Jobs": jobs})
    s3_stub.add_response(
        "get_bucket_lifecycle_configuration",
        lifecycle_response(expire_rule()),
        {"Bucket": "example-logs"},
    )
    s3_stub.add_response(
        "get_bucket_lifecycle_configuration",
        lifecycle_response(expire_rule("ui/")),
        {"Bucket": "other-logs"},
    )
    snapshots = collect_snapshots(glue, everything, 0, s3_client=s3)
    by_name = {s.name: s for s in snapshots}
    assert by_name["a"].event_logs == EventLogs(
        "example-logs", "spark-ui/", (LifecycleRule(True, "spark-ui/", True, False),)
    )
    assert by_name["b"].event_logs == by_name["a"].event_logs
    assert by_name["c"].event_logs.prefix == "other/"
    assert by_name["c"].event_logs.rules == by_name["a"].event_logs.rules
    assert by_name["d"].event_logs.bucket == "other-logs"


def test_ineligible_jobs_are_left_alone_and_cause_no_call(clients):
    glue, glue_stub, s3, s3_stub = clients
    jobs = [
        raw_job("ui-off", "s3://example-logs/spark-ui/", ui="false"),
        raw_job("no-path", None),
        raw_job("not-s3", "https://example.com/x"),
        raw_job("shell", "s3://example-logs/spark-ui/", command="pythonshell"),
    ]
    glue_stub.add_response("get_jobs", {"Jobs": jobs})  # no S3 response is queued
    snapshots = collect_snapshots(glue, everything, 0, s3_client=s3)
    assert all(s.event_logs is None and s.collect_issues == () for s in snapshots)


def test_mixed_jobs_sharing_a_bucket(clients):
    glue, glue_stub, s3, s3_stub = clients
    jobs = [
        raw_job("eligible", "s3://example-logs/spark-ui/"),
        raw_job("ui-off", "s3://example-logs/spark-ui/", ui="false"),
    ]
    glue_stub.add_response("get_jobs", {"Jobs": jobs})
    s3_stub.add_response("get_bucket_lifecycle_configuration", lifecycle_response(expire_rule()))
    eligible, ui_off = collect_snapshots(glue, everything, 0, s3_client=s3)
    assert eligible.event_logs is not None and ui_off.event_logs is None


def test_no_lifecycle_configuration_means_no_rules(clients):
    glue, glue_stub, s3, s3_stub = clients
    glue_stub.add_response("get_jobs", {"Jobs": [raw_job("a", "s3://example-logs/spark-ui/")]})
    s3_stub.add_client_error(
        "get_bucket_lifecycle_configuration",
        service_error_code="NoSuchLifecycleConfiguration",
        http_status_code=404,
    )
    [snap] = collect_snapshots(glue, everything, 0, s3_client=s3)
    assert snap.event_logs == EventLogs("example-logs", "spark-ui/", ())
    assert snap.collect_issues == ()


@pytest.mark.parametrize(
    "code, status",
    [("AccessDenied", 403), ("NoSuchBucket", 404), ("PermanentRedirect", 301)],
)
def test_unreadable_bucket_becomes_a_collect_issue_for_its_jobs_only(clients, code, status):
    glue, glue_stub, s3, s3_stub = clients
    jobs = [
        raw_job("denied", "s3://example-logs/spark-ui/"),
        raw_job("fine", "s3://other-logs/ui/"),
    ]
    glue_stub.add_response("get_jobs", {"Jobs": jobs})
    s3_stub.add_client_error(
        "get_bucket_lifecycle_configuration", service_error_code=code, http_status_code=status
    )
    s3_stub.add_response(
        "get_bucket_lifecycle_configuration", lifecycle_response(expire_rule("ui/"))
    )
    denied, fine = collect_snapshots(glue, everything, 0, s3_client=s3)
    assert denied.event_logs == EventLogs("example-logs", "spark-ui/", None)
    assert denied.collect_issues == (
        CollectIssue("GetBucketLifecycleConfiguration", code, "example-logs"),
    )
    assert fine.collect_issues == ()
    assert fine.event_logs.rules == (LifecycleRule(True, "ui/", True, False),)


def test_without_an_s3_client_nothing_is_collected(clients):
    glue, glue_stub, s3, s3_stub = clients
    glue_stub.add_response("get_jobs", {"Jobs": [raw_job("a", "s3://example-logs/spark-ui/")]})
    [snap] = collect_snapshots(glue, everything, 0)  # s3_client defaults to None
    assert snap.event_logs is None and snap.collect_issues == ()


# --- a bucket in another region -----------------------------------------------------------------
# When S3 answers with a redirect that does not name the bucket's region, botocore's region
# redirector calls HeadBucket to find it. The read-only guard blocks that call, which must never
# take the whole audit down. Stubber cannot reproduce this (it answers before the redirector runs),
# so the HTTP response is faked one level lower, in the `before-send` event.


class _Body:
    def __init__(self, data):
        self._data = data

    def stream(self):
        yield self._data


_REDIRECT = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b"<Error><Code>PermanentRedirect</Code><Message>redirect</Message></Error>"
)
_LIFECYCLE = (
    b'<?xml version="1.0" encoding="UTF-8"?><LifecycleConfiguration><Rule><ID>expire</ID>'
    b"<Filter><Prefix>ui/</Prefix></Filter><Status>Enabled</Status>"
    b"<Expiration><Days>30</Days></Expiration></Rule></LifecycleConfiguration>"
)


def _redirect_for_example_logs(request, **kwargs):
    """301 without any region information for example-logs; a normal answer for the rest."""
    if "example-logs" in request.url:
        return AWSResponse(request.url, 301, {"Content-Type": "application/xml"}, _Body(_REDIRECT))
    return AWSResponse(request.url, 200, {"Content-Type": "application/xml"}, _Body(_LIFECYCLE))


def test_redirect_without_a_region_is_a_collect_issue_not_a_crash():
    keys = {"aws_access_key_id": "t", "aws_secret_access_key": "t"}
    glue = boto3.client("glue", region_name="eu-central-1", **keys)
    s3 = boto3.client("s3", region_name="eu-central-1", **keys)
    install_read_only_guard(glue)
    install_read_only_guard(s3, service="s3", allowed=S3_ALLOWED_OPERATIONS)
    s3.meta.events.register(
        "before-send.s3.GetBucketLifecycleConfiguration", _redirect_for_example_logs
    )
    jobs = [
        raw_job("far", "s3://example-logs/spark-ui/"),
        raw_job("near", "s3://other-logs/ui/"),
    ]
    with Stubber(glue) as glue_stub:
        glue_stub.add_response("get_jobs", {"Jobs": jobs})
        far, near = collect_snapshots(glue, everything, 0, s3_client=s3)
    assert far.event_logs == EventLogs("example-logs", "spark-ui/", None)
    assert far.collect_issues == (
        CollectIssue("GetBucketLifecycleConfiguration", "BucketRegionUnresolved", "example-logs"),
    )
    assert near.collect_issues == ()
    assert near.event_logs.rules == (LifecycleRule(True, "ui/", True, False),)


def test_other_guard_violations_still_surface_as_bugs():
    class Client:
        def get_bucket_lifecycle_configuration(self, Bucket):
            raise ReadOnlyViolation("operation PutBucketLifecycleConfiguration is not allowed")

    with pytest.raises(ReadOnlyViolation, match="PutBucketLifecycleConfiguration"):
        _fetch_lifecycle(Client(), "example-logs")
