from glue_doctor.models import CollectIssue, JobSnapshot, RunSample

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
    )
