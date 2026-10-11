import pytest

from glue_doctor.checks.timeout import (
    GenerousParams,
    TightParams,
    check_generous,
    check_tight,
    nearest_rank,
)
from glue_doctor.models import Severity
from tests.factories import make_job, make_run


def runs(n, seconds=600, state="SUCCEEDED"):
    return [make_run(state=state, seconds=seconds) for _ in range(n)]


# --- timeout-too-generous -------------------------------------------------


def test_generous_flags_timeout_far_above_median():
    job = make_job(timeout=2880, runs=runs(20, seconds=600))
    [finding] = check_generous(job, GenerousParams())
    assert finding.check_id == "timeout-too-generous"
    assert finding.severity is Severity.WARNING
    assert finding.job == "example-job"
    assert finding.evidence["timeout_min"] == 2880
    assert finding.evidence["median_min"] == 10.0
    assert finding.evidence["runs_used"] == 20
    assert finding.evidence["ratio"] == 288.0


def test_generous_ratio_boundary_is_exclusive():
    # median 10 min, max_ratio 10 -> 100 min is allowed, 101 is not
    assert check_generous(make_job(timeout=100, runs=runs(5)), GenerousParams()) == []
    assert len(check_generous(make_job(timeout=101, runs=runs(5)), GenerousParams())) == 1


def test_generous_median_is_floored_at_one_minute():
    quick = runs(5, seconds=10)
    assert check_generous(make_job(timeout=10, runs=quick), GenerousParams()) == []
    assert len(check_generous(make_job(timeout=11, runs=quick), GenerousParams())) == 1


def test_generous_few_runs_falls_back_to_absolute_cap():
    job = make_job(timeout=2880, runs=runs(3))
    [finding] = check_generous(job, GenerousParams())
    assert finding.evidence["cap_minutes"] == 1440
    assert finding.evidence["runs_used"] == 3
    assert "ratio" not in finding.evidence


def test_generous_cap_boundary_is_exclusive():
    assert check_generous(make_job(timeout=1440, runs=runs(2)), GenerousParams()) == []
    assert len(check_generous(make_job(timeout=1441, runs=runs(2)), GenerousParams())) == 1


def test_generous_job_without_any_runs_uses_cap():
    assert len(check_generous(make_job(timeout=2880, runs=()), GenerousParams())) == 1
    assert check_generous(make_job(timeout=480, runs=()), GenerousParams()) == []


def test_generous_only_failed_runs_uses_cap():
    job = make_job(timeout=2880, runs=runs(10, state="FAILED"))
    [finding] = check_generous(job, GenerousParams())
    assert finding.evidence["runs_used"] == 0
    assert (
        check_generous(make_job(timeout=480, runs=runs(10, state="FAILED")), GenerousParams()) == []
    )


def test_generous_skips_when_runs_not_collected():
    assert check_generous(make_job(timeout=2880, runs=None), GenerousParams()) == []


def test_generous_skips_streaming_jobs():
    job = make_job(command="gluestreaming", timeout=2880, runs=runs(20))
    assert check_generous(job, GenerousParams()) == []


def test_generous_history_limits_the_sample():
    sample = runs(5, seconds=60) + runs(20, seconds=6000)
    [finding] = check_generous(make_job(timeout=100, runs=sample), GenerousParams(history=5))
    assert finding.evidence["runs_used"] == 5
    assert finding.evidence["median_min"] == 1.0


@pytest.mark.parametrize(
    "kwargs, field",
    [
        ({"max_ratio": 0}, "max_ratio"),
        ({"history": 0}, "history"),
        ({"min_runs": 0}, "min_runs"),
        ({"cap_minutes": 0}, "cap_minutes"),
        ({"max_ratio": float("nan")}, "max_ratio"),
        ({"max_ratio": float("inf")}, "max_ratio"),
        ({"history": 3, "min_runs": 5}, "min_runs"),
    ],
)
def test_generous_params_are_validated(kwargs, field):
    with pytest.raises(ValueError, match=field):
        GenerousParams(**kwargs)


# --- timeout-too-tight ----------------------------------------------------


def test_tight_flags_runs_close_to_timeout():
    sample = runs(18, seconds=600) + runs(2, seconds=1900)
    [finding] = check_tight(make_job(timeout=30, runs=sample), TightParams())
    assert finding.check_id == "timeout-too-tight"
    assert finding.severity is Severity.WARNING
    assert finding.evidence["timeout_min"] == 30
    assert finding.evidence["p95_min"] == pytest.approx(31.7, abs=0.1)


def test_tight_comfortable_timeout_has_no_finding():
    assert check_tight(make_job(timeout=480, runs=runs(20)), TightParams()) == []


def test_tight_boundary_is_exclusive():
    # p95 = 80 min, 0.8 * 100 = 80 -> not flagged
    assert check_tight(make_job(timeout=100, runs=runs(5, seconds=4800)), TightParams()) == []


def test_tight_needs_min_runs():
    assert check_tight(make_job(timeout=30, runs=runs(4, seconds=1700)), TightParams()) == []


def test_tight_counts_timeout_runs():
    sample = runs(5, seconds=28800, state="TIMEOUT")
    [finding] = check_tight(make_job(timeout=480, runs=sample), TightParams())
    assert finding.evidence["timeout_runs"] == 5


def test_tight_ignores_failed_runs():
    job = make_job(timeout=30, runs=runs(10, seconds=1700, state="FAILED"))
    assert check_tight(job, TightParams()) == []


def test_tight_skips_streaming_and_uncollected():
    streaming = make_job(command="gluestreaming", timeout=30, runs=runs(10, 1700))
    assert check_tight(streaming, TightParams()) == []
    assert check_tight(make_job(timeout=30, runs=None), TightParams()) == []


@pytest.mark.parametrize(
    "kwargs, field",
    [
        ({"p95_ratio": 0}, "p95_ratio"),
        ({"p95_ratio": 1.5}, "p95_ratio"),
        ({"p95_ratio": float("nan")}, "p95_ratio"),
        ({"history": 0}, "history"),
        ({"history": 3, "min_runs": 5}, "min_runs"),
    ],
)
def test_tight_params_are_validated(kwargs, field):
    with pytest.raises(ValueError, match=field):
        TightParams(**kwargs)


def test_nearest_rank():
    assert nearest_rank(list(range(1, 21)), 0.95) == 19
    assert nearest_rank([5], 0.95) == 5
