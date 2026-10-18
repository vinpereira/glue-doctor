"""The only module that talks to AWS. Everything it returns is plain, immutable data."""

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from glue_doctor import __version__
from glue_doctor.models import (
    SPARK_COMMANDS,
    CatalogScope,
    CatalogSnapshot,
    CollectIssue,
    EventLogs,
    JobSnapshot,
    LifecycleRule,
    OptimizerRun,
    OptimizerSnapshot,
    RunSample,
)
from glue_doctor.versions import parse_glue_version, resolve_timeout

ALLOWED_OPERATIONS = frozenset(
    {
        "GetJobs",
        "GetJobRuns",
        "GetDatabases",
        "GetTables",
        "BatchGetTableOptimizer",
        "ListTableOptimizerRuns",
    }
)
S3_ALLOWED_OPERATIONS = frozenset({"GetBucketLifecycleConfiguration"})

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
    s3: Any = None


def _describe(exc: Exception) -> str:
    """A short, safe description of an AWS error; never includes the raw response."""
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        return f"{exc.operation_name}: {code}"
    return f"{type(exc).__name__}: {exc}"


def _issue(operation: str, exc: Exception, target: str | None = None) -> CollectIssue:
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "Unknown")
    else:
        code = type(exc).__name__
    return CollectIssue(operation, code, target)


def install_read_only_guard(
    client: Any, service: str = "glue", allowed: frozenset[str] = ALLOWED_OPERATIONS
) -> None:
    def _guard(params: Any, model: Any, **kwargs: Any) -> None:
        if model.name not in allowed:
            raise ReadOnlyViolation(f"operation {model.name} is not allowed")

    client.meta.events.register(f"before-parameter-build.{service}.*", _guard)


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
        s3 = session.client("s3", config=_BOTO_CONFIG)
        install_read_only_guard(s3, service="s3", allowed=S3_ALLOWED_OPERATIONS)
        account_id = account_id_of(session.client("sts"))
    except (BotoCoreError, ValueError) as exc:  # e.g. ProfileNotFound or an invalid endpoint
        raise AuditAborted(_describe(exc)) from exc
    return AwsContext(client=glue, region=region_name, account_id=account_id, s3=s3)


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


def event_log_target(job: JobSnapshot) -> tuple[str, str] | None:
    """The (bucket, prefix) where a Spark job writes its UI event logs, if it does.

    The prefix is empty for the bucket root and otherwise ends with "/", because Glue writes the
    files inside that location.
    """
    if job.command not in SPARK_COMMANDS:
        return None
    if job.special_args.get("--enable-spark-ui", "").strip().lower() != "true":
        return None
    path = job.special_args.get("--spark-event-logs-path", "").strip()
    if not path.startswith("s3://"):
        return None
    bucket, _, prefix = path[len("s3://") :].partition("/")
    if not bucket:
        return None
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return bucket, prefix


def parse_lifecycle_rules(raw_rules: list[dict[str, Any]]) -> tuple[LifecycleRule, ...]:
    """Reduce S3 lifecycle rules to the facts needed to decide whether logs are expired."""
    parsed = []
    for raw in raw_rules:
        flt = raw.get("Filter") or {}
        both = flt.get("And") or {}
        expiration = raw.get("Expiration") or {}
        parsed.append(
            LifecycleRule(
                enabled=raw.get("Status") == "Enabled",
                prefix=flt.get("Prefix") or both.get("Prefix") or raw.get("Prefix") or "",
                expires=expiration.get("Days") is not None or expiration.get("Date") is not None,
                scoped=any(k in flt for k in ("Tag", "ObjectSizeGreaterThan", "ObjectSizeLessThan"))
                or any(k in both for k in ("Tags", "ObjectSizeGreaterThan", "ObjectSizeLessThan")),
            )
        )
    return tuple(parsed)


def _fetch_lifecycle(s3_client: Any, bucket: str) -> tuple[LifecycleRule, ...] | CollectIssue:
    operation = "GetBucketLifecycleConfiguration"
    try:
        response = s3_client.get_bucket_lifecycle_configuration(Bucket=bucket)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "NoSuchLifecycleConfiguration":
            return ()
        return _issue(operation, exc, bucket)
    except BotoCoreError as exc:
        return _issue(operation, exc, bucket)
    except ReadOnlyViolation as exc:
        # For a bucket in another region, botocore's redirector calls HeadBucket when the redirect
        # does not name the region. The guard blocks it (the allowlist stays strict); that bucket
        # is reported as unreadable instead of failing the whole audit. Any other violation is a
        # bug and must surface.
        if "HeadBucket" in str(exc):
            return CollectIssue(operation, "BucketRegionUnresolved", bucket)
        raise
    return parse_lifecycle_rules(response.get("Rules", []))


def _attach_event_logs(snapshots: list[JobSnapshot], s3_client: Any) -> list[JobSnapshot]:
    targets = {snapshot.name: event_log_target(snapshot) for snapshot in snapshots}
    buckets = sorted({target[0] for target in targets.values() if target})
    if not buckets:
        return snapshots
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        fetched = pool.map(lambda bucket: _fetch_lifecycle(s3_client, bucket), buckets)
        results = dict(zip(buckets, fetched, strict=True))
    attached = []
    for snapshot in snapshots:
        target = targets[snapshot.name]
        if target is None:
            attached.append(snapshot)
            continue
        bucket, prefix = target
        result = results[bucket]
        if isinstance(result, CollectIssue):
            attached.append(
                replace(
                    snapshot,
                    event_logs=EventLogs(bucket, prefix, None),
                    collect_issues=(*snapshot.collect_issues, result),
                )
            )
        else:
            attached.append(replace(snapshot, event_logs=EventLogs(bucket, prefix, result)))
    return attached


def collect_snapshots(
    client: Any,
    job_filter: Callable[[str], bool],
    wanted_runs: int,
    s3_client: Any = None,
) -> list[JobSnapshot]:
    try:
        raw_jobs = [job for job in _fetch_jobs(client) if job_filter(job["Name"])]
    except (BotoCoreError, ClientError) as exc:
        raise AuditAborted(_describe(exc)) from exc
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        snapshots = list(pool.map(lambda raw: _collect_one(client, raw, wanted_runs), raw_jobs))
    if s3_client is not None:
        snapshots = _attach_event_logs(snapshots, s3_client)
    return snapshots


OPTIMIZER_TYPES = ("compaction", "retention", "orphan_file_deletion")
BATCH_LIMIT = 20  # BatchGetTableOptimizer accepts at most 20 entries per call
HISTORY_FLOOR = 10  # runs kept for a failing optimizer, so a streak can be reported up to this


def _list_databases(client: Any) -> list[dict[str, Any]]:
    return [
        database
        for page in client.get_paginator("get_databases").paginate()
        for database in page["DatabaseList"]
    ]


def _iceberg_tables(
    client: Any, database: dict[str, Any]
) -> tuple[list[tuple[str, str, str | None]], CollectIssue | None]:
    """The Iceberg tables of one database as (database, table, catalog id), or the read failure."""
    name = database["Name"]
    found: list[tuple[str, str, str | None]] = []
    try:
        for page in client.get_paginator("get_tables").paginate(DatabaseName=name):
            for table in page["TableList"]:
                if (table.get("Parameters") or {}).get("table_type", "").upper() == "ICEBERG":
                    catalog_id = table.get("CatalogId") or database.get("CatalogId")
                    found.append((name, table["Name"], catalog_id))
    except (BotoCoreError, ClientError) as exc:
        return [], _issue("GetTables", exc, name)
    return found, None


def _to_optimizer_run(raw: dict[str, Any], now: datetime) -> OptimizerRun:
    ended = raw.get("endTimestamp") or raw.get("startTimestamp")
    return OptimizerRun(
        event_type=str(raw.get("eventType", "")).lower(),
        ended_on=ended,
        age_days=(now - ended).days if ended else None,
        has_error=bool(raw.get("error")),
    )


def _batch_get(
    client: Any, entries: list[dict[str, str]], now: datetime
) -> tuple[list[OptimizerSnapshot], CollectIssue | None]:
    """One BatchGetTableOptimizer call: the configured optimizers, or the whole-call failure."""
    try:
        response = client.batch_get_table_optimizer(Entries=entries)
    except (BotoCoreError, ClientError) as exc:
        return [], _issue("BatchGetTableOptimizer", exc, "catalog")
    snapshots = []
    for item in response.get("TableOptimizers", []):
        optimizer = item.get("tableOptimizer") or {}
        last = optimizer.get("lastRun")
        snapshots.append(
            OptimizerSnapshot(
                database=item["databaseName"],
                table=item["tableName"],
                optimizer_type=optimizer.get("type", ""),
                enabled=bool((optimizer.get("configuration") or {}).get("enabled")),
                last_run=_to_optimizer_run(last, now) if last else None,
            )
        )
    for failure in response.get("Failures", []):
        code = (failure.get("error") or {}).get("ErrorCode", "Unknown")
        if code == "EntityNotFoundException":
            continue  # the optimizer is simply not configured for that table
        snapshots.append(
            OptimizerSnapshot(
                database=failure["databaseName"],
                table=failure["tableName"],
                optimizer_type=failure.get("type", ""),
                enabled=False,
                last_run=None,
                collect_issues=(
                    CollectIssue(
                        "BatchGetTableOptimizer",
                        code,
                        f"{failure['databaseName']}.{failure['tableName']}",
                    ),
                ),
            )
        )
    return snapshots, None


def _history(
    client: Any, optimizer: OptimizerSnapshot, catalog_id: str | None, limit: int, now: datetime
) -> OptimizerSnapshot:
    """Read the run history of a failing optimizer; the API ordering is not trusted."""
    try:
        runs = [
            run
            for page in client.get_paginator("list_table_optimizer_runs").paginate(
                CatalogId=catalog_id,
                DatabaseName=optimizer.database,
                TableName=optimizer.table,
                Type=optimizer.optimizer_type,
            )
            for run in page["TableOptimizerRuns"]
        ]
    except (BotoCoreError, ClientError) as exc:
        issue = _issue("ListTableOptimizerRuns", exc, optimizer.resource)
        return replace(optimizer, collect_issues=(*optimizer.collect_issues, issue))
    epoch = datetime.min.replace(tzinfo=UTC)
    runs.sort(key=lambda r: r.get("startTimestamp") or epoch, reverse=True)
    return replace(optimizer, recent_runs=tuple(_to_optimizer_run(r, now) for r in runs[:limit]))


def _merge_scopes(issues: list[CollectIssue]) -> tuple[CatalogScope, ...]:
    grouped: dict[str, list[CollectIssue]] = {}
    for issue in issues:
        bucket = grouped.setdefault(issue.target or "catalog", [])
        if issue not in bucket:
            bucket.append(issue)
    return tuple(CatalogScope(resource, tuple(found)) for resource, found in grouped.items())


def collect_catalog(
    client: Any,
    database_filter: Callable[[str], bool],
    min_failures: int,
    now: datetime | None = None,
) -> CatalogSnapshot:
    """Scan the Data Catalog for Iceberg tables and read their configured optimizers."""
    now = now or datetime.now(UTC)
    limit = max(min_failures, HISTORY_FLOOR)
    issues: list[CollectIssue] = []
    try:
        databases = [d for d in _list_databases(client) if database_filter(d["Name"])]
    except (BotoCoreError, ClientError) as exc:
        return CatalogSnapshot((), _merge_scopes([_issue("GetDatabases", exc, "catalog")]), 0)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        listed = list(pool.map(lambda db: _iceberg_tables(client, db), databases))
        tables: list[tuple[str, str, str | None]] = []
        for found, issue in listed:
            tables.extend(found)
            if issue is not None:
                issues.append(issue)
        catalog_ids = {(db, table): cid for db, table, cid in tables}
        entries = [
            {"catalogId": cid, "databaseName": db, "tableName": table, "type": kind}
            for db, table, cid in tables
            for kind in OPTIMIZER_TYPES
        ]
        chunks = [entries[i : i + BATCH_LIMIT] for i in range(0, len(entries), BATCH_LIMIT)]
        optimizers: list[OptimizerSnapshot] = []
        for found, call_issue in pool.map(lambda c: _batch_get(client, c, now), chunks):
            optimizers.extend(found)
            if call_issue is not None:
                issues.append(call_issue)
        suspects = [
            o for o in optimizers if o.last_run is not None and o.last_run.event_type == "failed"
        ]
        refreshed = dict(
            zip(
                [id(o) for o in suspects],
                pool.map(
                    lambda o: _history(
                        client, o, catalog_ids.get((o.database, o.table)), limit, now
                    ),
                    suspects,
                ),
                strict=True,
            )
        )
    optimizers = [refreshed.get(id(o), o) for o in optimizers]
    return CatalogSnapshot(tuple(optimizers), _merge_scopes(issues), len(tables))
