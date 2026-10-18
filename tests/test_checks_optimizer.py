import pytest

from glue_doctor.checks.iceberg_optimizer import OptimizerParams, check_optimizer
from glue_doctor.models import Severity
from tests.factories import make_optimizer


def run(optimizer, **params):
    return check_optimizer(optimizer, OptimizerParams(**params))


# --- last-run-failed ------------------------------------------------------------------------


def test_two_consecutive_failures_are_reported():
    optimizer = make_optimizer(
        "compaction", last_event="failed", recent=("failed", "failed", "completed")
    )
    [finding] = run(optimizer)
    assert finding.check_id == "iceberg-optimizer-suspended"
    assert finding.severity is Severity.WARNING
    assert (finding.resource_type, finding.resource) == ("table", "example_db.example_table")
    assert finding.evidence["reason"] == "last-run-failed"
    assert finding.evidence["consecutive_failures"] == 2
    assert finding.evidence["optimizer_type"] == "compaction"
    assert "last 2 runs failed" in finding.message


def test_a_single_failure_is_below_the_default_threshold():
    optimizer = make_optimizer("retention", last_event="failed", recent=("failed", "completed"))
    assert run(optimizer) == []


def test_a_completed_last_run_is_never_a_failure():
    assert run(make_optimizer("retention", last_event="completed", recent=None)) == []


def test_a_streak_broken_by_a_completed_run_does_not_count_older_failures():
    # newest first: one failure, then a completed run, then an older failure
    optimizer = make_optimizer(
        "compaction", last_event="failed", recent=("failed", "completed", "failed")
    )
    assert run(optimizer) == []


def test_threshold_is_configurable():
    optimizer = make_optimizer("compaction", last_event="failed", recent=("failed", "completed"))
    assert len(run(optimizer, min_failures=1)) == 1
    three = make_optimizer(
        "compaction", last_event="failed", recent=("failed", "failed", "completed")
    )
    assert run(three, min_failures=3) == []


def test_streak_falls_back_to_the_last_run_when_history_is_missing():
    optimizer = make_optimizer("compaction", last_event="failed", recent=None)
    assert run(optimizer) == []  # one known failure, below 2
    assert len(run(optimizer, min_failures=1)) == 1


def test_disabled_optimizer_with_failures_says_so_without_saying_suspended():
    optimizer = make_optimizer(
        "compaction",
        enabled=False,
        last_event="failed",
        recent=("failed", "failed"),
        has_error=True,
    )
    [finding] = run(optimizer)
    assert "also disabled" in finding.message
    assert finding.evidence["enabled"] is False and finding.evidence["has_error"] is True
    everything = finding.message + finding.remediation + str(finding.evidence)
    assert "suspend" not in everything.lower()


@pytest.mark.parametrize("kind", ["compaction", "retention", "orphan_file_deletion"])
def test_failures_are_reported_for_every_type(kind):
    optimizer = make_optimizer(kind, last_event="failed", recent=("failed", "failed"))
    assert len(run(optimizer)) == 1


# --- no-recent-run --------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["retention", "orphan_file_deletion"])
def test_daily_optimizers_that_stopped_are_reported(kind):
    [finding] = run(make_optimizer(kind, last_event="completed", age_days=12))
    assert finding.evidence["reason"] == "no-recent-run"
    assert finding.evidence["last_run_age_days"] == 12
    assert "12 days" in finding.message


def test_stale_boundary_is_exclusive():
    assert run(make_optimizer("retention", age_days=7)) == []
    assert len(run(make_optimizer("retention", age_days=8))) == 1
    assert len(run(make_optimizer("retention", age_days=3), stale_days=2)) == 1


def test_compaction_is_never_reported_for_no_recent_run():
    assert run(make_optimizer("compaction", last_event="completed", age_days=400)) == []


def test_a_disabled_stale_optimizer_is_not_reported():
    assert run(make_optimizer("retention", enabled=False, age_days=30)) == []


def test_a_single_old_failure_does_not_hide_a_stopped_daily_optimizer():
    optimizer = make_optimizer(
        "retention", last_event="failed", age_days=30, recent=("failed", "completed")
    )
    [finding] = run(optimizer)
    assert finding.evidence["reason"] == "no-recent-run"
    assert finding.evidence["last_run_age_days"] == 30


def test_a_below_threshold_streak_still_reports_a_stopped_daily_optimizer():
    optimizer = make_optimizer(
        "orphan_file_deletion", last_event="failed", age_days=20, recent=("failed", "failed")
    )
    [finding] = run(optimizer, min_failures=3)
    assert finding.evidence["reason"] == "no-recent-run"


def test_a_single_old_failure_of_compaction_is_still_not_reported():
    optimizer = make_optimizer(
        "compaction", last_event="failed", age_days=30, recent=("failed", "completed")
    )
    assert run(optimizer) == []


def test_a_failure_streak_wins_over_the_stale_rule():
    optimizer = make_optimizer(
        "retention", last_event="failed", age_days=30, recent=("failed", "failed")
    )
    [finding] = run(optimizer)
    assert finding.evidence["reason"] == "last-run-failed"


# --- what is never reported -----------------------------------------------------------------


@pytest.mark.parametrize("kind", ["compaction", "retention", "orphan_file_deletion"])
def test_an_optimizer_that_never_ran_is_not_reported(kind):
    assert run(make_optimizer(kind, last_event=None)) == []


def test_disabled_alone_is_not_reported():
    optimizer = make_optimizer("compaction", enabled=False, last_event="completed", age_days=1)
    assert run(optimizer) == []


def test_in_progress_and_starting_runs_are_not_failures():
    assert run(make_optimizer("retention", last_event="in_progress", age_days=0)) == []
    assert run(make_optimizer("retention", last_event="starting", age_days=0)) == []


def test_run_without_an_age_is_not_stale():
    assert run(make_optimizer("retention", last_event="completed", age_days=None)) == []


@pytest.mark.parametrize(
    "kwargs, field",
    [({"min_failures": 0}, "min_failures"), ({"stale_days": 0}, "stale_days")],
)
def test_params_are_validated(kwargs, field):
    with pytest.raises(ValueError, match=field):
        OptimizerParams(**kwargs)
