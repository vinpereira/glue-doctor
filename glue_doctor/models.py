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
class JobSnapshot:
    name: str
    command: str
    glue_version: tuple[int, int] | None
    timeout_min_effective: int
    timeout_source: str
    special_args: dict[str, str]
    runs: tuple[RunSample, ...] | None = None
    collect_issues: tuple[CollectIssue, ...] = ()


@dataclass(frozen=True)
class Finding:
    check_id: str
    severity: Severity
    job: str
    message: str
    remediation: str
    evidence: dict[str, Any] = field(default_factory=dict)
