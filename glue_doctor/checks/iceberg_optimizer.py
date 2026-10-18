from dataclasses import dataclass

from glue_doctor.checks.base import CheckSpec
from glue_doctor.models import Finding, OptimizerSnapshot, Severity

CHECK_ID = "iceberg-optimizer-suspended"
DAILY_TYPES = frozenset({"retention", "orphan_file_deletion"})


@dataclass(frozen=True)
class OptimizerParams:
    min_failures: int = 2
    stale_days: int = 7

    def __post_init__(self) -> None:
        if self.min_failures < 1:
            raise ValueError("min_failures must be at least 1")
        if self.stale_days < 1:
            raise ValueError("stale_days must be at least 1")


def _failure_streak(optimizer: OptimizerSnapshot) -> int:
    runs = optimizer.recent_runs or ((optimizer.last_run,) if optimizer.last_run else ())
    streak = 0
    for run in runs:  # newest first
        if run.event_type != "failed":
            break
        streak += 1
    return streak


def _evidence(optimizer: OptimizerSnapshot, reason: str, **extra: object) -> dict[str, object]:
    last = optimizer.last_run
    return {
        "database": optimizer.database,
        "table": optimizer.table,
        "optimizer_type": optimizer.optimizer_type,
        "enabled": optimizer.enabled,
        "last_run_age_days": last.age_days if last else None,
        "has_error": bool(last and last.has_error),
        "reason": reason,
        **extra,
    }


_REMEDIATION = (
    "Open the optimizer's run history (Glue console or ListTableOptimizerRuns), fix the cause "
    "(the optimizer's IAM role, Lake Formation permissions, or the table and S3 state) and "
    "re-enable it if it is disabled"
)


def check_optimizer(optimizer: OptimizerSnapshot, params: OptimizerParams) -> list[Finding]:
    run = optimizer.last_run
    if run is None:
        return []  # never ran: new, or (for compaction) threshold driven, so not a signal
    kind = optimizer.optimizer_type
    if run.event_type == "failed":
        streak = _failure_streak(optimizer)
        if streak >= params.min_failures:
            message = f"{kind} optimizer on {optimizer.resource}: the last {streak} runs failed"
            if not optimizer.enabled:
                message += " (the optimizer is also disabled)"
            return [
                Finding(
                    check_id=CHECK_ID,
                    severity=Severity.WARNING,
                    resource_type="table",
                    resource=optimizer.resource,
                    message=message,
                    remediation=_REMEDIATION,
                    evidence=_evidence(optimizer, "last-run-failed", consecutive_failures=streak),
                )
            ]
        # below the threshold: a daily optimizer that then stopped is still caught below
    if (
        kind in DAILY_TYPES
        and optimizer.enabled
        and run.age_days is not None
        and run.age_days > params.stale_days
    ):
        return [
            Finding(
                check_id=CHECK_ID,
                severity=Severity.WARNING,
                resource_type="table",
                resource=optimizer.resource,
                message=(
                    f"{kind} optimizer on {optimizer.resource}: no run for {run.age_days} days "
                    "(it normally runs daily)"
                ),
                remediation=_REMEDIATION,
                evidence=_evidence(optimizer, "no-recent-run"),
            )
        ]
    return []


ICEBERG_OPTIMIZER_SUSPENDED = CheckSpec(
    id=CHECK_ID,
    description="Iceberg table optimizer with failing or stopped runs",
    default_severity=Severity.WARNING,
    params_cls=OptimizerParams,
    run=check_optimizer,
    needs_catalog=True,
    subject="optimizer",
)
