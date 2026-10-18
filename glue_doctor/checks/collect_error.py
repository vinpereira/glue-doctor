from dataclasses import dataclass

from glue_doctor.checks.base import CheckSpec
from glue_doctor.models import (
    CatalogScope,
    CollectIssue,
    Finding,
    JobSnapshot,
    OptimizerSnapshot,
    Severity,
)

_DENIED = frozenset({"AccessDenied", "AccessDeniedException", "AllAccessDisabled"})
_CATALOG_OPERATIONS = frozenset(
    {"GetDatabases", "GetTables", "BatchGetTableOptimizer", "ListTableOptimizerRuns"}
)
_S3_HINT = (
    "Grant s3:GetLifecycleConfiguration on the event-log bucket, or set "
    "[checks.spark-ui-no-lifecycle] enabled = false; the audit of this resource is incomplete"
)
_CATALOG_HINT = (
    "Grant glue:GetDatabases, glue:GetTables, glue:BatchGetTableOptimizer and "
    "glue:ListTableOptimizerRuns, or set [checks.iceberg-optimizer-suspended] enabled = false; "
    "the audit of this resource is incomplete"
)
_GENERIC_HINT = (
    "Grant the missing read permission or retry; the audit of this resource is incomplete"
)


@dataclass(frozen=True)
class NoParams:
    """Checks without tunable parameters use this empty dataclass."""


def _hint(issue: CollectIssue) -> str:
    if issue.code in _DENIED:
        if issue.operation == "GetBucketLifecycleConfiguration":
            return _S3_HINT
        if issue.operation in _CATALOG_OPERATIONS:
            return _CATALOG_HINT
    return _GENERIC_HINT


def _identify(subject: JobSnapshot | OptimizerSnapshot | CatalogScope) -> tuple[str, str]:
    if isinstance(subject, JobSnapshot):
        return "job", subject.name
    if isinstance(subject, OptimizerSnapshot):
        return "table", subject.resource
    return "catalog", subject.resource


def check_collect_error(
    subject: JobSnapshot | OptimizerSnapshot | CatalogScope, params: NoParams
) -> list[Finding]:
    resource_type, resource = _identify(subject)
    findings = []
    for issue in subject.collect_issues:
        evidence = {"operation": issue.operation, "error_code": issue.code}
        if issue.target is not None:
            evidence["target"] = issue.target
        findings.append(
            Finding(
                check_id="collect-error",
                severity=Severity.WARNING,
                resource_type=resource_type,
                resource=resource,
                message=(
                    f"Could not read data for this resource ({issue.operation}: {issue.code}); "
                    "checks that need it were skipped"
                ),
                remediation=_hint(issue),
                evidence=evidence,
            )
        )
    return findings


COLLECT_ERROR = CheckSpec(
    id="collect-error",
    description="A resource's data could not be read (audit incomplete)",
    default_severity=Severity.WARNING,
    params_cls=NoParams,
    run=check_collect_error,
    subject="any",
)
