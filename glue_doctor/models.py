from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

SPARK_COMMANDS = frozenset({"glueetl", "gluestreaming"})


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"

    @property
    def rank(self) -> int:
        return _RANK[self.value]


_RANK = {"info": 1, "warning": 2, "error": 3}


@dataclass(frozen=True)
class RunSample:
    state: str
    started_on: datetime | None
    execution_seconds: int
    timeout_min: int | None


@dataclass(frozen=True)
class CollectIssue:
    operation: str
    code: str


@dataclass(frozen=True)
class LifecycleRule:
    """One S3 lifecycle rule, reduced to what matters for expiring Spark UI event logs."""

    enabled: bool
    prefix: str  # empty means the whole bucket
    expires: bool  # removes current objects (Expiration with Days or Date)
    scoped: bool  # filtered by tag or object size, so it reaches only part of the objects


@dataclass(frozen=True)
class EventLogs:
    """Where a job writes its Spark UI event logs and which lifecycle rules apply there.

    `rules` is None when the bucket could not be read (the job also has a CollectIssue) and an
    empty tuple when the bucket has no lifecycle configuration.
    """

    bucket: str
    prefix: str  # empty for the bucket root, otherwise ends with "/"
    rules: tuple[LifecycleRule, ...] | None


@dataclass(frozen=True)
class JobSnapshot:
    name: str
    command: str
    glue_version: tuple[int, int] | None
    timeout_min_effective: int
    timeout_source: str
    special_args: dict[str, str]
    runs: tuple[RunSample, ...] | None = None
    collect_issues: tuple[CollectIssue, ...] = ()
    event_logs: EventLogs | None = None


@dataclass(frozen=True)
class Finding:
    check_id: str
    severity: Severity
    resource_type: str  # "job", "table" or "catalog"
    resource: str  # a job name, "database.table", or "catalog" / a database name
    message: str
    remediation: str
    evidence: dict[str, Any] = field(default_factory=dict)
