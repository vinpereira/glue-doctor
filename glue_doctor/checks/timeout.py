import math
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import median

from glue_doctor.checks.base import CheckSpec
from glue_doctor.models import Finding, JobSnapshot, Severity

SUCCEEDED = "SUCCEEDED"
TIMEOUT = "TIMEOUT"
STREAMING = "gluestreaming"


def nearest_rank(sorted_values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile of an ascending sequence."""
    rank = max(1, math.ceil(round(q * len(sorted_values), 9)))
    return sorted_values[rank - 1]


@dataclass(frozen=True)
class GenerousParams:
    max_ratio: float = 10.0
    history: int = 20
    min_runs: int = 5
    cap_minutes: int = 1440
    min_timeout_minutes: int = 120

    def __post_init__(self) -> None:
        if not math.isfinite(self.max_ratio) or self.max_ratio <= 0:
            raise ValueError("max_ratio must be a finite number greater than 0")
        if self.history < 1:
            raise ValueError("history must be at least 1")
        if self.min_runs < 1:
            raise ValueError("min_runs must be at least 1")
        if self.min_runs > self.history:
            raise ValueError("min_runs must not exceed history")
        if self.cap_minutes < 1:
            raise ValueError("cap_minutes must be at least 1")
        if self.min_timeout_minutes < 1:
            raise ValueError("min_timeout_minutes must be at least 1")


def check_generous(job: JobSnapshot, params: GenerousParams) -> list[Finding]:
    if job.command == STREAMING or job.runs is None:
        return []
    timeout = job.timeout_min_effective
    successes = [r for r in job.runs if r.state == SUCCEEDED][: params.history]
    base = {
        "timeout_min": timeout,
        "timeout_source": job.timeout_source,
        "runs_used": len(successes),
    }
    if len(successes) >= params.min_runs:
        typical = max(median(r.execution_seconds for r in successes) / 60, 1.0)
        ratio = timeout / typical
        # A short timeout (say 60 min) on a 2 min job is a sensible safety net, not a risk: only
        # timeouts above the minimum are worth a finding, however large the ratio is.
        if timeout <= params.min_timeout_minutes or ratio <= params.max_ratio:
            return []
        evidence = {**base, "median_min": round(typical, 1), "ratio": round(ratio, 1)}
        message = (
            f"Timeout is {timeout} min but the median successful run takes "
            f"{typical:.0f} min ({ratio:.0f}x)"
        )
    elif timeout > params.cap_minutes:
        evidence = {**base, "cap_minutes": params.cap_minutes}
        message = (
            f"Timeout is {timeout} min (above {params.cap_minutes} min) and only "
            f"{len(successes)} successful run(s) are available to compare against"
        )
    else:
        return []
    return [
        Finding(
            check_id="timeout-too-generous",
            severity=Severity.WARNING,
            resource_type="job",
            resource=job.name,
            message=message,
            remediation=(
                "Set the timeout closer to the observed runtime so a hung run is stopped early"
            ),
            evidence=evidence,
        )
    ]


@dataclass(frozen=True)
class TightParams:
    p95_ratio: float = 0.8
    history: int = 20
    min_runs: int = 5

    def __post_init__(self) -> None:
        if not 0 < self.p95_ratio <= 1:
            raise ValueError("p95_ratio must be greater than 0 and at most 1")
        if self.history < 1:
            raise ValueError("history must be at least 1")
        if self.min_runs < 1:
            raise ValueError("min_runs must be at least 1")
        if self.min_runs > self.history:
            raise ValueError("min_runs must not exceed history")


def check_tight(job: JobSnapshot, params: TightParams) -> list[Finding]:
    if job.command == STREAMING or job.runs is None:
        return []
    sample = [r for r in job.runs if r.state in (SUCCEEDED, TIMEOUT)][: params.history]
    if len(sample) < params.min_runs:
        return []
    timeout = job.timeout_min_effective
    p95_min = nearest_rank(sorted(r.execution_seconds for r in sample), 0.95) / 60
    if p95_min <= params.p95_ratio * timeout:
        return []
    timeout_runs = sum(1 for r in sample if r.state == TIMEOUT)
    return [
        Finding(
            check_id="timeout-too-tight",
            severity=Severity.WARNING,
            resource_type="job",
            resource=job.name,
            message=(
                f"The slowest 5% of recent runs take {p95_min:.0f} min, close to the "
                f"{timeout} min timeout ({timeout_runs} of {len(sample)} runs timed out)"
            ),
            remediation="Raise the timeout or speed the job up before runs start failing",
            evidence={
                "timeout_min": timeout,
                "timeout_source": job.timeout_source,
                "p95_min": round(p95_min, 1),
                "runs_used": len(sample),
                "timeout_runs": timeout_runs,
            },
        )
    ]


TIMEOUT_TOO_GENEROUS = CheckSpec(
    id="timeout-too-generous",
    description="Timeout far above the median runtime of recent runs",
    default_severity=Severity.WARNING,
    params_cls=GenerousParams,
    run=check_generous,
    needs_runs=True,
)

TIMEOUT_TOO_TIGHT = CheckSpec(
    id="timeout-too-tight",
    description="Recent runs take most of the timeout",
    default_severity=Severity.WARNING,
    params_cls=TightParams,
    run=check_tight,
    needs_runs=True,
)
