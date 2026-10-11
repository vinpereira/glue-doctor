import boto3
import pytest
from botocore.stub import Stubber

from glue_doctor import collector
from glue_doctor.checks.observability import FLAGS
from glue_doctor.collector import (
    SPECIAL_ARGS_ALLOWLIST,
    AuditAborted,
    ReadOnlyViolation,
    account_id_of,
    build_snapshot,
    collect_snapshots,
    connect,
    install_read_only_guard,
)


def raw_job(name="etl-a", **extra):
    base = {
        "Name": name,
        "Command": {"Name": "glueetl"},
        "GlueVersion": "5.1",
        "Timeout": 60,
        "DefaultArguments": {},
    }
    return {**base, **extra}


def raw_run(state="SUCCEEDED", seconds=600):
    return {"Id": "jr_x", "JobRunState": state, "ExecutionTime": seconds, "Timeout": 60}


@pytest.fixture(autouse=True)
def single_worker(monkeypatch):
    # Stubber queues responses in call order, so keep collection sequential in tests.
    monkeypatch.setattr(collector, "MAX_WORKERS", 1)


@pytest.fixture
def glue():
    client = boto3.client(
        "glue", region_name="eu-central-1", aws_access_key_id="t", aws_secret_access_key="t"
    )
    install_read_only_guard(client)
    with Stubber(client) as stubber:
        yield client, stubber
        stubber.assert_no_pending_responses()


def everything(_name):
    return True


def test_build_snapshot_maps_fields():
    snap = build_snapshot(raw_job(Timeout=60, GlueVersion="6.0"))
    assert (snap.name, snap.command, snap.glue_version) == ("etl-a", "glueetl", (6, 0))
    assert (snap.timeout_min_effective, snap.timeout_source) == (60, "explicit")
    assert snap.runs is None and snap.collect_issues == ()


def test_build_snapshot_keeps_only_allowlisted_arguments():
    args = {"--enable-metrics": "true", "--db_pw": "hunter2", "--api-token": "abc"}
    snap = build_snapshot(raw_job(DefaultArguments=args))
    assert snap.special_args == {"--enable-metrics": "true"}
    assert "hunter2" not in repr(snap) and "abc" not in repr(snap)


def test_build_snapshot_without_version_or_timeout():
    snap = build_snapshot({"Name": "x", "Command": {"Name": "glueetl"}})
    assert snap.glue_version is None
    assert (snap.timeout_min_effective, snap.timeout_source) == (2880, "assumed-default")


def test_build_snapshot_future_version_uses_modern_default():
    snap = build_snapshot({"Name": "x", "Command": {"Name": "glueetl"}, "GlueVersion": "7.0"})
    assert snap.glue_version == (7, 0)
    assert (snap.timeout_min_effective, snap.timeout_source) == (480, "default")


def test_allowlist_covers_observability_flags():
    assert set(FLAGS.values()) <= SPECIAL_ARGS_ALLOWLIST


def test_get_jobs_is_paginated(glue):
    client, stubber = glue
    stubber.add_response("get_jobs", {"Jobs": [raw_job("a")], "NextToken": "t1"})
    stubber.add_response("get_jobs", {"Jobs": [raw_job("b")]})
    snapshots = collect_snapshots(client, everything, 0)
    assert [s.name for s in snapshots] == ["a", "b"]


def test_no_job_runs_calls_when_not_wanted(glue):
    client, stubber = glue
    stubber.add_response("get_jobs", {"Jobs": [raw_job("a")]})
    [snap] = collect_snapshots(client, everything, 0)
    assert snap.runs is None


def test_runs_are_collected_and_mapped(glue):
    client, stubber = glue
    stubber.add_response("get_jobs", {"Jobs": [raw_job("a")]})
    stubber.add_response(
        "get_job_runs",
        {"JobRuns": [raw_run("SUCCEEDED", 600), raw_run("TIMEOUT", 28800)]},
        {"JobName": "a", "MaxResults": 200},
    )
    [snap] = collect_snapshots(client, everything, 20)
    assert [(r.state, r.execution_seconds, r.timeout_min) for r in snap.runs] == [
        ("SUCCEEDED", 600, 60),
        ("TIMEOUT", 28800, 60),
    ]


def test_run_without_execution_time_defaults_to_zero(glue):
    client, stubber = glue
    stubber.add_response("get_jobs", {"Jobs": [raw_job("a")]})
    stubber.add_response("get_job_runs", {"JobRuns": [{"Id": "jr_x", "JobRunState": "RUNNING"}]})
    [snap] = collect_snapshots(client, everything, 20)
    assert (snap.runs[0].state, snap.runs[0].execution_seconds) == ("RUNNING", 0)


def test_job_without_runs_has_empty_tuple(glue):
    client, stubber = glue
    stubber.add_response("get_jobs", {"Jobs": [raw_job("a")]})
    stubber.add_response("get_job_runs", {"JobRuns": []})
    [snap] = collect_snapshots(client, everything, 20)
    assert snap.runs == ()


def test_collection_stops_once_enough_relevant_runs(glue):
    client, stubber = glue
    stubber.add_response("get_jobs", {"Jobs": [raw_job("a")]})
    page = {"JobRuns": [raw_run(), raw_run(), raw_run()], "NextToken": "more"}
    stubber.add_response("get_job_runs", page)  # no second page is queued
    [snap] = collect_snapshots(client, everything, 2)
    assert len(snap.runs) == 3
    assert snap.collect_issues == ()


def test_job_filter_skips_runs_of_excluded_jobs(glue):
    client, stubber = glue
    stubber.add_response("get_jobs", {"Jobs": [raw_job("keep"), raw_job("skip")]})
    stubber.add_response("get_job_runs", {"JobRuns": []})
    snapshots = collect_snapshots(client, lambda name: name == "keep", 20)
    assert [s.name for s in snapshots] == ["keep"]


def test_get_job_runs_failure_becomes_a_collect_issue(glue):
    client, stubber = glue
    stubber.add_response("get_jobs", {"Jobs": [raw_job("a"), raw_job("b")]})
    stubber.add_client_error("get_job_runs", service_error_code="AccessDeniedException")
    stubber.add_response("get_job_runs", {"JobRuns": []})
    first, second = collect_snapshots(client, everything, 20)
    assert first.runs is None
    assert [(i.operation, i.code) for i in first.collect_issues] == [
        ("GetJobRuns", "AccessDeniedException")
    ]
    assert second.runs == () and second.collect_issues == ()


def test_get_jobs_failure_aborts_without_leaking_the_response(glue):
    client, stubber = glue
    stubber.add_client_error(
        "get_jobs",
        service_error_code="AccessDeniedException",
        service_message="User arn:aws:iam::123456789012:user/example is not authorized",
    )
    with pytest.raises(AuditAborted) as excinfo:
        collect_snapshots(client, everything, 20)
    assert "GetJobs: AccessDeniedException" in str(excinfo.value)
    assert "not authorized" not in str(excinfo.value)


def test_guard_blocks_mutating_operations():
    client = boto3.client(
        "glue", region_name="eu-central-1", aws_access_key_id="t", aws_secret_access_key="t"
    )
    install_read_only_guard(client)
    with pytest.raises(ReadOnlyViolation, match="UpdateJob"):
        client.update_job(JobName="etl-a", JobUpdate={})
    with pytest.raises(ReadOnlyViolation, match="StartJobRun"):
        client.start_job_run(JobName="etl-a")


def test_account_id_of():
    sts = boto3.client(
        "sts", region_name="eu-central-1", aws_access_key_id="t", aws_secret_access_key="t"
    )
    with Stubber(sts) as stubber:
        stubber.add_response(
            "get_caller_identity",
            {"UserId": "u", "Account": "123456789012", "Arn": "arn:aws:iam::123456789012:user/x"},
        )
        assert account_id_of(sts) == "123456789012"
    with Stubber(sts) as stubber:
        stubber.add_client_error("get_caller_identity", service_error_code="AccessDenied")
        assert account_id_of(sts) is None


@pytest.fixture
def no_aws_config(monkeypatch, tmp_path):
    for var in ("AWS_DEFAULT_REGION", "AWS_REGION", "AWS_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "credentials"))


def test_connect_requires_a_region(no_aws_config):
    with pytest.raises(AuditAborted, match="region"):
        connect(None, None)


def test_connect_rejects_an_empty_region_from_the_environment(no_aws_config, monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "")
    with pytest.raises(AuditAborted, match="region"):
        connect(None, None)


def test_connect_rejects_an_empty_region_argument(no_aws_config):
    with pytest.raises(AuditAborted, match="region"):
        connect(None, "")


def test_connect_rejects_a_malformed_region(no_aws_config):
    with pytest.raises(AuditAborted, match="region"):
        connect(None, "not a region")


def test_connect_reports_unknown_profile(no_aws_config):
    with pytest.raises(AuditAborted, match="ProfileNotFound"):
        connect("does-not-exist", "eu-central-1")
