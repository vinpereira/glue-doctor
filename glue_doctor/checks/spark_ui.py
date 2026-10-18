from glue_doctor.checks.base import CheckSpec
from glue_doctor.checks.collect_error import NoParams
from glue_doctor.models import SPARK_COMMANDS, Finding, JobSnapshot, LifecycleRule, Severity


def _covers(rule: LifecycleRule, prefix: str) -> bool:
    """The rule removes every object under `prefix`."""
    return rule.enabled and rule.expires and not rule.scoped and prefix.startswith(rule.prefix)


def _partial(rule: LifecycleRule, prefix: str) -> bool:
    """An enabled, expiring rule that reaches only part of the objects under `prefix`."""
    if not (rule.enabled and rule.expires):
        return False
    if rule.scoped:  # a tag or size filter: whatever its prefix, it skips some objects
        return rule.prefix == "" or prefix.startswith(rule.prefix) or rule.prefix.startswith(prefix)
    return rule.prefix.startswith(prefix) and len(rule.prefix) > len(prefix)


def check_spark_ui(job: JobSnapshot, params: NoParams) -> list[Finding]:
    logs = job.event_logs
    if logs is None or logs.rules is None or job.command not in SPARK_COMMANDS:
        return []  # an unreadable bucket is reported by the collect-error check
    if any(_covers(rule, logs.prefix) for rule in logs.rules):
        return []
    reason = "no-lifecycle-configuration" if not logs.rules else "no-covering-rule"
    partial = sum(1 for rule in logs.rules if _partial(rule, logs.prefix))
    location = f"s3://{logs.bucket}/{logs.prefix}"
    return [
        Finding(
            check_id="spark-ui-no-lifecycle",
            severity=Severity.WARNING,
            resource_type="job",
            resource=job.name,
            message=(
                f"Spark UI event logs at {location} are never expired "
                "(no lifecycle rule removes them)"
            ),
            remediation=(
                "Add an S3 lifecycle rule that expires objects under the prefix (for example "
                "after 30 days) so event logs do not grow without bound"
            ),
            evidence={
                "bucket": logs.bucket,
                "prefix": logs.prefix,
                "reason": reason,
                "partial_rules": partial,
            },
        )
    ]


SPARK_UI_NO_LIFECYCLE = CheckSpec(
    id="spark-ui-no-lifecycle",
    description="Spark UI event logs in S3 are never expired",
    default_severity=Severity.WARNING,
    params_cls=NoParams,
    run=check_spark_ui,
    needs_event_log_lifecycle=True,
)
