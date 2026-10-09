from dataclasses import dataclass

from glue_doctor.checks.base import CheckSpec
from glue_doctor.models import SPARK_COMMANDS, Finding, JobSnapshot, Severity
from glue_doctor.versions import LATEST_KNOWN, format_version, parse_glue_version, runtime_of


@dataclass(frozen=True)
class VersionParams:
    warn_below: str = "5.0"
    info_below: str = "5.1"

    def __post_init__(self) -> None:
        for name in ("warn_below", "info_below"):
            if parse_glue_version(getattr(self, name)) is None:
                raise ValueError(f"{name}: invalid Glue version {getattr(self, name)!r}")


def check_version(job: JobSnapshot, params: VersionParams) -> list[Finding]:
    if job.command not in SPARK_COMMANDS or job.glue_version is None:
        return []
    version = job.glue_version
    warn_below = parse_glue_version(params.warn_below)
    info_below = parse_glue_version(params.info_below)
    if version < warn_below:
        severity, target = Severity.WARNING, warn_below
    elif version < info_below:
        severity, target = Severity.INFO, info_below
    else:
        return []
    runtime = runtime_of(version)
    detail = f" (Spark {runtime[0]}, Python {runtime[1]})" if runtime else ""
    return [
        Finding(
            check_id="glue-version",
            severity=severity,
            job=job.name,
            message=f"Glue {format_version(version)}{detail} is older than "
            f"{format_version(target)}",
            remediation=(
                f"Upgrade to Glue {format_version(target)} or newer. Glue "
                f"{format_version(LATEST_KNOWN)} runs Spark 4 (ANSI SQL mode on by default) "
                "and costs less, so validate your job before migrating"
            ),
            evidence={"glue_version": format_version(version), "minimum": format_version(target)},
        )
    ]


GLUE_VERSION = CheckSpec(
    id="glue-version",
    description="Glue version older than the configured minimum",
    default_severity=Severity.WARNING,
    params_cls=VersionParams,
    run=check_version,
)
