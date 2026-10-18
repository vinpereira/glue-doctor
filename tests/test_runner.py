import tomllib
from datetime import date

import pytest

from glue_doctor.checks import REGISTRY
from glue_doctor.config import default_config, parse_config
from glue_doctor.models import CatalogScope, CatalogSnapshot, CollectIssue, Finding, Severity
from glue_doctor.runner import exit_code_for, run_checks
from tests.factories import make_job, make_optimizer

TODAY = date(2026, 10, 8)


def cfg(text=""):
    return parse_config(tomllib.loads(text))


def only(*enabled):
    """A config with every check disabled except the given ones."""
    lines = [f"[checks.{c}]\nenabled = false" for c in REGISTRY if c not in enabled]
    return "\n".join(lines)


def noisy_job(name="example-job"):
    # timeout 2880 with no runs -> generous warning; no flags -> observability info
    return make_job(name=name, timeout=2880, runs=(), special_args={})


def test_clean_job_has_no_findings():
    result = run_checks([make_job(runs=())], default_config(), TODAY)
    assert result.findings == () and result.suppressed == () and result.jobs_audited == 1


def test_findings_are_sorted_by_severity_then_job():
    result = run_checks([noisy_job("b"), noisy_job("a")], default_config(), TODAY)
    assert [(f.severity.value, f.resource) for f in result.findings] == [
        ("warning", "a"),
        ("warning", "b"),
        ("info", "a"),
        ("info", "b"),
    ]


def test_disabled_check_produces_nothing():
    result = run_checks([noisy_job()], cfg(only("observability-off")), TODAY)
    assert [f.check_id for f in result.findings] == ["observability-off"]


def test_severity_override_applies_to_the_check():
    config = cfg('[checks.observability-off]\nseverity = "error"')
    result = run_checks([noisy_job()], config, TODAY)
    severities = {f.check_id: f.severity for f in result.findings}
    assert severities["observability-off"] is Severity.ERROR
    assert severities["timeout-too-generous"] is Severity.WARNING


def test_suppression_moves_matching_findings_with_reason():
    config = cfg(
        '[[suppress]]\ncheck = "observability-off"\nresource = "legacy-*"\nreason = "known gap"'
    )
    result = run_checks([noisy_job("legacy-a"), noisy_job("new-b")], config, TODAY)
    assert [(s.finding.resource, s.reason) for s in result.suppressed] == [
        ("legacy-a", "known gap")
    ]
    remaining = {f.resource for f in result.findings if f.check_id == "observability-off"}
    assert remaining == {"new-b"}


def test_suppression_until_today_is_still_active():
    config = cfg('[[suppress]]\ncheck = "observability-off"\nreason = "x"\nuntil = 2026-10-08')
    result = run_checks([noisy_job()], config, TODAY)
    assert len(result.suppressed) == 1 and result.warnings == ()


def test_expired_suppression_returns_the_finding_and_warns_once():
    config = cfg('[[suppress]]\ncheck = "observability-off"\nreason = "x"\nuntil = 2026-10-07')
    result = run_checks([noisy_job("a"), noisy_job("b")], config, TODAY)
    assert result.suppressed == ()
    assert {f.resource for f in result.findings if f.check_id == "observability-off"} == {"a", "b"}
    assert len(result.warnings) == 1
    assert "expired on 2026-10-07" in result.warnings[0]


def test_expired_suppression_that_matches_nothing_does_not_warn():
    config = cfg('[[suppress]]\ncheck = "glue-version"\nreason = "x"\nuntil = 2020-01-01')
    assert run_checks([noisy_job()], config, TODAY).warnings == ()


def test_collect_error_flows_through_like_any_other_check():
    job = make_job(collect_issues=(CollectIssue("GetJobRuns", "ThrottlingException"),))
    result = run_checks([job], default_config(), TODAY)
    assert [f.check_id for f in result.findings] == ["collect-error"]


@pytest.mark.parametrize(
    "severity, fail_on, expected",
    [
        ("warning", "warning", 1),
        ("warning", "error", 0),
        ("info", "warning", 0),
        ("info", "info", 1),
        ("error", "warning", 1),
        ("error", "never", 0),
    ],
)
def test_exit_code_for(severity, fail_on, expected):
    finding = Finding("x", Severity(severity), "job", "example-job", "m", "r")
    assert exit_code_for([finding], fail_on) == expected


def test_exit_code_without_findings_is_zero():
    assert exit_code_for([], "info") == 0


def test_optimizer_findings_use_their_own_subject_and_count_tables():
    broken = make_optimizer("compaction", last_event="failed", recent=("failed", "failed"))
    catalog = CatalogSnapshot((broken,), (), 5)
    result = run_checks([make_job(runs=())], default_config(), TODAY, catalog=catalog)
    [finding] = result.findings
    assert (finding.check_id, finding.resource_type) == ("iceberg-optimizer-suspended", "table")
    assert result.tables_audited == 5 and result.jobs_audited == 1


def test_job_checks_never_run_on_optimizers():
    only_jobs = cfg(only("timeout-too-generous"))
    catalog = CatalogSnapshot((make_optimizer("retention", age_days=30),), (), 1)
    assert run_checks([], only_jobs, TODAY, catalog=catalog).findings == ()


def test_catalog_scopes_become_collect_errors():
    issue = CollectIssue("GetDatabases", "AccessDeniedException", "catalog")
    scope = CatalogScope("catalog", (issue,))
    result = run_checks([], default_config(), TODAY, catalog=CatalogSnapshot((), (scope,), 0))
    [finding] = result.findings
    assert (finding.check_id, finding.resource_type, finding.resource) == (
        "collect-error",
        "catalog",
        "catalog",
    )


def test_optimizer_findings_can_be_suppressed_by_resource_glob():
    broken = make_optimizer("compaction", last_event="failed", recent=("failed", "failed"))
    config = cfg(
        '[[suppress]]\ncheck = "iceberg-optimizer-suspended"\n'
        'resource = "example_db.*"\nreason = "known"'
    )
    result = run_checks([], config, TODAY, catalog=CatalogSnapshot((broken,), (), 1))
    assert result.findings == () and len(result.suppressed) == 1
