from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from glue_doctor.models import Finding, JobSnapshot, Severity


@dataclass(frozen=True)
class CheckSpec:
    """A registered check.

    `params_cls` is a dataclass whose fields are the check's TOML parameters; it validates
    itself in `__post_init__` (raising ValueError). Checks with `needs_runs=True` must expose a
    `history` field, used to decide how many runs the collector fetches. Checks with
    `needs_event_log_lifecycle=True` make the collector read the S3 lifecycle configuration
    of each Spark UI event-log bucket. Checks with `needs_catalog=True` make it scan the Data
    Catalog, and `subject` says which snapshots the check runs on.
    """

    id: str
    description: str
    default_severity: Severity
    params_cls: type
    run: Callable[[JobSnapshot, Any], list[Finding]]
    needs_runs: bool = False
    needs_event_log_lifecycle: bool = False
    needs_catalog: bool = False
    subject: str = "job"  # "job", "optimizer" or "any" (every snapshot kind, for collect-error)
