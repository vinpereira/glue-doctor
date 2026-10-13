"""The only module that talks to AWS. Everything it returns is plain, immutable data."""

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from typing import Any

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from glue_doctor import __version__
from glue_doctor.models import CollectIssue, JobSnapshot, RunSample
from glue_doctor.versions import parse_glue_version, resolve_timeout

ALLOWED_OPERATIONS = frozenset({"GetJobs", "GetJobRuns"})

# Glue-defined special arguments. User-defined arguments never enter a snapshot, so they cannot
# reach findings, evidence or logs.
SPECIAL_ARGS_ALLOWLIST = frozenset(
    {
        "--enable-metrics",
        "--enable-observability-metrics",
        "--enable-job-insights",
        "--enable-continuous-cloudwatch-log",
        "--enable-spark-ui",
        "--spark-event-logs-path",
        "--job-bookmark-option",
    }
)

MAX_WORKERS = 8
_BOTO_CONFIG = BotoConfig(
    retries={"mode": "adaptive", "max_attempts": 10},
    user_agent_extra=f"glue-doctor/{__version__}",
)


class ReadOnlyViolation(Exception):
    """An AWS operation outside the allowlist was attempted (a bug, never a user error)."""


class AuditAborted(Exception):
    """The audit cannot proceed (no credentials, GetJobs denied, no region); exit code 3."""


@dataclass(frozen=True)
class AwsContext:
    client: Any
    region: str
    account_id: str | None


def _describe(exc: Exception) -> str:
    """A short, safe description of an AWS error; never includes the raw response."""
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        return f"{exc.operation_name}: {code}"
    return f"{type(exc).__name__}: {exc}"


def _issue(operation: str, exc: Exception) -> CollectIssue:
    if isinstance(exc, ClientError):
        return CollectIssue(operation, exc.response.get("Error", {}).get("Code", "Unknown"))
    return CollectIssue(operation, type(exc).__name__)


def install_read_only_guard(client: Any) -> None:
    def _guard(params: Any, model: Any, **kwargs: Any) -> None:
        if model.name not in ALLOWED_OPERATIONS:
            raise ReadOnlyViolation(f"operation {model.name} is not allowed")

    client.meta.events.register("before-parameter-build.glue.*", _guard)


def account_id_of(sts_client: Any) -> str | None:
    try:
        return sts_client.get_caller_identity()["Account"]
    except (BotoCoreError, ClientError):
        return None


def connect(profile: str | None, region: str | None) -> AwsContext:
    try:
        session = boto3.Session(profile_name=profile, region_name=region)
        region_name = session.region_name
        if not region_name:
            raise AuditAborted("no AWS region configured; pass --region or set AWS_DEFAULT_REGION")
        glue = session.client("glue", config=_BOTO_CONFIG)
        install_read_only_guard(glue)
        account_id = account_id_of(session.client("sts"))
    except (BotoCoreError, ValueError) as exc:  # e.g. ProfileNotFound or an invalid endpoint
        raise AuditAborted(_describe(exc)) from exc
    return AwsContext(client=glue, region=region_name, account_id=account_id)


def build_snapshot(raw: dict[str, Any]) -> JobSnapshot:
    version = parse_glue_version(raw.get("GlueVersion"))
    timeout, source = resolve_timeout(raw.get("Timeout"), version)
    arguments = raw.get("DefaultArguments") or {}
    return JobSnapshot(
        name=raw["Name"],
        command=(raw.get("Command") or {}).get("Name", ""),
        glue_version=version,
        timeout_min_effective=timeout,
        timeout_source=source,
        special_args={k: v for k, v in arguments.items() if k in SPECIAL_ARGS_ALLOWLIST},
    )


def _to_run(raw: dict[str, Any]) -> RunSample:
    return RunSample(
        state=raw.get("JobRunState", ""),
        started_on=raw.get("StartedOn"),
        execution_seconds=int(raw.get("ExecutionTime", 0)),
        timeout_min=raw.get("Timeout"),
    )


def _fetch_jobs(client: Any) -> Iterator[dict[str, Any]]:
    for page in client.get_paginator("get_jobs").paginate():
        yield from page["Jobs"]


def _fetch_runs(client: Any, job_name: str, wanted: int) -> tuple[RunSample, ...]:
    """Newest-first runs; stops once `wanted` SUCCEEDED runs have been seen.

    Counting only SUCCEEDED runs is enough for every check: each of them needs at most `wanted`
    runs in SUCCEEDED (and, for the tight-timeout check, TIMEOUT) state, and a SUCCEEDED run also
    counts as one of those. TIMEOUT runs alone must not end the paging.
    """
    runs: list[RunSample] = []
    succeeded = 0
    paginator = client.get_paginator("get_job_runs")
    for page in paginator.paginate(JobName=job_name, PaginationConfig={"PageSize": 200}):
        for raw in page["JobRuns"]:
            run = _to_run(raw)
            runs.append(run)
            if run.state == "SUCCEEDED":
                succeeded += 1
        if succeeded >= wanted:
            break
    return tuple(runs)


def _collect_one(client: Any, raw: dict[str, Any], wanted_runs: int) -> JobSnapshot:
    snapshot = build_snapshot(raw)
    if wanted_runs <= 0:
        return snapshot
    try:
        return replace(snapshot, runs=_fetch_runs(client, snapshot.name, wanted_runs))
    except (BotoCoreError, ClientError) as exc:
        return replace(snapshot, collect_issues=(_issue("GetJobRuns", exc),))


def collect_snapshots(
    client: Any, job_filter: Callable[[str], bool], wanted_runs: int
) -> list[JobSnapshot]:
    try:
        raw_jobs = [job for job in _fetch_jobs(client) if job_filter(job["Name"])]
    except (BotoCoreError, ClientError) as exc:
        raise AuditAborted(_describe(exc)) from exc
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        return list(pool.map(lambda raw: _collect_one(client, raw, wanted_runs), raw_jobs))
