import fnmatch
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date

from glue_doctor.checks import REGISTRY
from glue_doctor.config import Config, Suppression
from glue_doctor.models import Finding, JobSnapshot, Severity


@dataclass(frozen=True)
class SuppressedFinding:
    finding: Finding
    reason: str


@dataclass(frozen=True)
class AuditResult:
    findings: tuple[Finding, ...]
    suppressed: tuple[SuppressedFinding, ...]
    jobs_audited: int
    warnings: tuple[str, ...]


def _matching(finding: Finding, suppressions: Sequence[Suppression]) -> list[Suppression]:
    return [
        s
        for s in suppressions
        if s.check == finding.check_id and fnmatch.fnmatchcase(finding.job, s.job)
    ]


def run_checks(snapshots: Sequence[JobSnapshot], config: Config, today: date) -> AuditResult:
    findings: list[Finding] = []
    suppressed: list[SuppressedFinding] = []
    expired_hits: set[Suppression] = set()

    for snapshot in snapshots:
        for check_id, spec in REGISTRY.items():
            settings = config.checks[check_id]
            if not settings.enabled:
                continue
            for finding in spec.run(snapshot, settings.params):
                if settings.severity is not None:
                    finding = replace(finding, severity=settings.severity)
                matches = _matching(finding, config.suppressions)
                active = [s for s in matches if s.until is None or today <= s.until]
                if active:
                    suppressed.append(SuppressedFinding(finding, active[0].reason))
                else:
                    findings.append(finding)
                    expired_hits.update(matches)

    findings.sort(key=lambda f: (-f.severity.rank, f.job, f.check_id))
    warnings = sorted(
        f"suppression for '{s.check}' on '{s.job}' expired on {s.until.isoformat()}; "
        "its findings are reported again"
        for s in expired_hits
    )
    return AuditResult(
        findings=tuple(findings),
        suppressed=tuple(suppressed),
        jobs_audited=len(snapshots),
        warnings=tuple(warnings),
    )


def exit_code_for(findings: Sequence[Finding], fail_on: str) -> int:
    if fail_on == "never":
        return 0
    threshold = Severity(fail_on).rank
    return 1 if any(f.severity.rank >= threshold for f in findings) else 0
