from glue_doctor.models import (
    CollectIssue,
    EventLogs,
    JobSnapshot,
    OptimizerRun,
    OptimizerSnapshot,
    RunSample,
)

OBSERVABLE_ARGS = {
    "--enable-job-insights": "true",
    "--enable-metrics": "true",
    "--enable-continuous-cloudwatch-log": "true",
}


def make_run(state: str = "SUCCEEDED", seconds: int = 600, timeout_min: int | None = None):
    return RunSample(
        state=state, started_on=None, execution_seconds=seconds, timeout_min=timeout_min
    )


def make_job(
    name: str = "example-job",
    command: str = "glueetl",
    glue_version: tuple[int, int] | None = (5, 1),
    timeout: int = 480,
    timeout_source: str = "explicit",
    special_args: dict[str, str] | None = None,
    runs: list[RunSample] | tuple[RunSample, ...] | None = None,
    collect_issues: tuple[CollectIssue, ...] = (),
    event_logs: EventLogs | None = None,
) -> JobSnapshot:
    return JobSnapshot(
        name=name,
        command=command,
        glue_version=glue_version,
        timeout_min_effective=timeout,
        timeout_source=timeout_source,
        special_args=dict(OBSERVABLE_ARGS) if special_args is None else special_args,
        runs=None if runs is None else tuple(runs),
        collect_issues=collect_issues,
        event_logs=event_logs,
    )


def make_optimizer(
    optimizer_type: str = "retention",
    database: str = "example_db",
    table: str = "example_table",
    enabled: bool = True,
    last_event: str | None = "completed",
    age_days: int | None = 1,
    recent: tuple[str, ...] | None = None,
    has_error: bool = False,
    collect_issues: tuple[CollectIssue, ...] = (),
) -> OptimizerSnapshot:
    """`recent` lists event types newest first; `last_event=None` means the optimizer never ran."""
    last_run = None
    if last_event is not None:
        last_run = OptimizerRun(last_event, None, age_days, has_error)
    recent_runs = None
    if recent is not None:
        recent_runs = tuple(OptimizerRun(e, None, age_days, has_error) for e in recent)
    return OptimizerSnapshot(
        database=database,
        table=table,
        optimizer_type=optimizer_type,
        enabled=enabled,
        last_run=last_run,
        recent_runs=recent_runs,
        collect_issues=collect_issues,
    )
