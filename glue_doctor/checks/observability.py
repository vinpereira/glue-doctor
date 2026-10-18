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


def _is_missing(option: str, job: JobSnapshot) -> bool:
    """Whether `option` is off, following how Glue itself reads each argument."""
    flag = FLAGS[option]
    args = job.special_args
    version = job.glue_version
    if option == "metrics":
        # Presence-based flag: any value (even empty) enables it, removing the key disables it.
        return flag not in args
    if option == "job-insights":
        if version is not None and version < (2, 0):
            return False  # job run insights needs Glue 2.0 or later
        # On by default: only an explicit "false" turns it off.
        return args.get(flag, "true").strip().lower() == "false"
    # continuous-logging: deprecated from Glue 5.0, where every job has real-time logging.
    if version is None or version >= (5, 0):
        return False
    return args.get(flag, "").strip().lower() != "true"


def check_observability(job: JobSnapshot, params: ObservabilityParams) -> list[Finding]:
    if job.command not in SPARK_COMMANDS:
        return []
    missing = [FLAGS[option] for option in params.require if _is_missing(option, job)]
    if not missing:
        return []
    return [
        Finding(
            check_id="observability-off",
            severity=Severity.INFO,
            resource_type="job",
            resource=job.name,
            message=f"Observability settings are off: {', '.join(missing)}",
            remediation=(
                "Enable these job parameters so failures and resource usage can be diagnosed "
                "(--enable-metrics is a presence-only flag and takes no value)"
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
