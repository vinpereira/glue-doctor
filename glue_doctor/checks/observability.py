from dataclasses import dataclass

from glue_doctor.checks.base import CheckSpec
from glue_doctor.models import SPARK_COMMANDS, Finding, JobSnapshot, Severity

FLAGS = {
    "job-insights": "--enable-job-insights",
    "metrics": "--enable-metrics",
    "continuous-logging": "--enable-continuous-cloudwatch-log",
}


@dataclass(frozen=True)
class ObservabilityParams:
    require: tuple[str, ...] = ("job-insights", "metrics", "continuous-logging")

    def __post_init__(self) -> None:
        unknown = [name for name in self.require if name not in FLAGS]
        if unknown:
            raise ValueError(
                f"require: unknown option(s) {', '.join(unknown)}; allowed: {', '.join(FLAGS)}"
            )


def check_observability(job: JobSnapshot, params: ObservabilityParams) -> list[Finding]:
    if job.command not in SPARK_COMMANDS:
        return []
    missing = []
    for option in params.require:
        if option == "job-insights" and job.glue_version is not None and job.glue_version < (2, 0):
            continue  # job run insights needs Glue 2.0 or later
        flag = FLAGS[option]
        if job.special_args.get(flag, "").strip().lower() != "true":
            missing.append(flag)
    if not missing:
        return []
    return [
        Finding(
            check_id="observability-off",
            severity=Severity.INFO,
            job=job.name,
            message=f"Observability settings are off: {', '.join(missing)}",
            remediation=(
                "Set these job arguments to true so failures and resource usage can be diagnosed"
            ),
            evidence={"missing": missing},
        )
    ]


OBSERVABILITY_OFF = CheckSpec(
    id="observability-off",
    description="Job insights, metrics or continuous logs are off",
    default_severity=Severity.INFO,
    params_cls=ObservabilityParams,
    run=check_observability,
)
