from dataclasses import dataclass

from glue_doctor.checks.base import CheckSpec
from glue_doctor.models import Finding, JobSnapshot, Severity


@dataclass(frozen=True)
class NoParams:
    """Checks without tunable parameters use this empty dataclass."""


def check_collect_error(job: JobSnapshot, params: NoParams) -> list[Finding]:
    return [
        Finding(
            check_id="collect-error",
            severity=Severity.WARNING,
            job=job.name,
            message=(
                f"Could not read data for this job ({issue.operation}: {issue.code}); "
                "checks that need it were skipped"
            ),
            remediation=(
                "Grant the missing read permission or retry; the audit of this job is incomplete"
            ),
            evidence={"operation": issue.operation, "error_code": issue.code},
        )
        for issue in job.collect_issues
    ]


COLLECT_ERROR = CheckSpec(
    id="collect-error",
    description="A job's data could not be read (audit incomplete)",
    default_severity=Severity.WARNING,
    params_cls=NoParams,
    run=check_collect_error,
)
