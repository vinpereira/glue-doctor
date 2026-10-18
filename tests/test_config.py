import tomllib
from datetime import date

import pytest

from glue_doctor.checks import REGISTRY
from glue_doctor.config import (
    ConfigError,
    default_config,
    discover_config_path,
    load_config,
    needs_event_log_lifecycle,
    parse_config,
    runs_needed,
)
from glue_doctor.models import Severity


def parse(text):
    return parse_config(tomllib.loads(text))


def test_defaults():
    cfg = default_config()
    assert cfg.fail_on == "warning"
    assert cfg.include == ("*",) and cfg.exclude == ()
    assert set(cfg.checks) == set(REGISTRY)
    assert all(s.enabled and s.severity is None for s in cfg.checks.values())
    assert cfg.suppressions == ()


def test_job_selected_uses_include_and_exclude_globs():
    cfg = parse('[jobs]\ninclude = ["etl-*"]\nexclude = ["etl-tmp-*"]')
    assert cfg.job_selected("etl-orders")
    assert not cfg.job_selected("etl-tmp-1")
    assert not cfg.job_selected("other")


def test_full_file():
    cfg = parse(
        """
fail_on = "error"
region = "eu-west-1"
profile = "p"

[checks.timeout-too-generous]
severity = "error"
max_ratio = 5
history = 10

[checks.observability-off]
enabled = false
require = ["metrics"]

[[suppress]]
check = "glue-version"
resource = "legacy-*"
reason = "pending migration"
until = 2027-01-31
"""
    )
    assert (cfg.fail_on, cfg.region, cfg.profile) == ("error", "eu-west-1", "p")
    generous = cfg.checks["timeout-too-generous"]
    assert generous.severity is Severity.ERROR
    assert generous.params.max_ratio == 5.0 and isinstance(generous.params.max_ratio, float)
    assert generous.params.history == 10
    obs = cfg.checks["observability-off"]
    assert obs.enabled is False and obs.params.require == ("metrics",)
    sup = cfg.suppressions[0]
    assert (sup.check, sup.resource, sup.reason, sup.until) == (
        "glue-version",
        "legacy-*",
        "pending migration",
        date(2027, 1, 31),
    )


def test_min_timeout_minutes_is_configurable():
    cfg = parse("[checks.timeout-too-generous]\nmin_timeout_minutes = 30")
    assert cfg.checks["timeout-too-generous"].params.min_timeout_minutes == 30
    assert default_config().checks["timeout-too-generous"].params.min_timeout_minutes == 120


def test_suppression_job_defaults_to_all_jobs():
    cfg = parse('[[suppress]]\ncheck = "glue-version"\nreason = "x"')
    assert cfg.suppressions[0].resource == "*" and cfg.suppressions[0].until is None


@pytest.mark.parametrize(
    "text, fragment",
    [
        ("bogus = 1", "bogus"),
        ('fail_on = "high"', "fail_on"),
        ("[jobs]\nincl = []", "incl"),
        ("[checks.nope]\nenabled = true", "nope"),
        ("[checks.timeout-too-generous]\nbogus = 1", "bogus"),
        ('[checks.timeout-too-generous]\nmax_ratio = "ten"', "max_ratio"),
        ("[checks.timeout-too-generous]\nhistory = 0", "history"),
        ('[checks.timeout-too-generous]\nseverity = "loud"', "severity"),
        ('[checks.timeout-too-generous]\nenabled = "yes"', "enabled"),
        ('[checks.glue-version]\nwarn_below = "abc"', "warn_below"),
        ('[[suppress]]\ncheck = "glue-version"', "reason"),
        ('[[suppress]]\ncheck = "glue-version"\nreason = "  "', "reason"),
        ('[[suppress]]\ncheck = "nope"\nreason = "x"', "nope"),
        ('region = ""', "region"),
        ('profile = "  "', "profile"),
        ("[jobs]\ninclude = []", "include"),
        ('[jobs]\ninclude = [""]', "include"),
        ('[jobs]\nexclude = [" "]', "exclude"),
        ('[[suppress]]\ncheck = ["glue-version"]\nreason = "x"', "check"),
        ('[[suppress]]\ncheck = "glue-version"\nreason = "x"\nresource = ""', "resource"),
        (
            '[[suppress]]\ncheck = "glue-version"\nreason = "x"\njob = "legacy-*"',
            "renamed to 'resource'",
        ),
        (
            '[[suppress]]\ncheck = "glue-version"\nreason = "x"\nbogus = 1',
            "allowed: check, reason, resource, until",
        ),
        ("[checks.timeout-too-generous]\nmin_runs = 30", "min_runs"),
        ("[checks.timeout-too-tight]\nmin_runs = 30", "min_runs"),
        ("[checks.timeout-too-generous]\nmax_ratio = nan", "max_ratio"),
        ('[checks.glue-version]\nwarn_below = "5.1"\ninfo_below = "4.0"', "warn_below"),
        ('[[suppress]]\ncheck = "glue-version"\nreason = "x"\nuntil = "2027-01-31"', "until"),
        (
            '[[suppress]]\ncheck = "glue-version"\nreason = "x"\nuntil = 2027-01-31T10:00:00',
            "until",
        ),
    ],
)
def test_invalid_config_is_rejected(text, fragment):
    with pytest.raises(ConfigError, match=fragment):
        parse(text)


def test_load_config_reads_a_file(tmp_path):
    path = tmp_path / "glue-doctor.toml"
    path.write_text('fail_on = "info"\n')
    assert load_config(path).fail_on == "info"


def test_load_config_without_path_returns_defaults():
    assert load_config(None).fail_on == "warning"


def test_load_config_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "missing.toml")


def test_load_config_invalid_toml(tmp_path):
    path = tmp_path / "glue-doctor.toml"
    path.write_text("fail_on = ")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config(path)


def test_load_config_non_utf8_file(tmp_path):
    path = tmp_path / "glue-doctor.toml"
    path.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(path)


def test_load_config_path_is_a_directory(tmp_path):
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(tmp_path)


def test_discover_config_path(tmp_path):
    explicit = tmp_path / "custom.toml"
    assert discover_config_path(explicit, cwd=tmp_path) == explicit
    assert discover_config_path(None, cwd=tmp_path) is None
    (tmp_path / "glue-doctor.toml").write_text("")
    assert discover_config_path(None, cwd=tmp_path) == tmp_path / "glue-doctor.toml"


def test_runs_needed():
    assert runs_needed(default_config()) == 20
    assert runs_needed(parse("[checks.timeout-too-generous]\nhistory = 50")) == 50
    off = parse(
        "[checks.timeout-too-generous]\nenabled = false\n"
        "[checks.timeout-too-tight]\nenabled = false"
    )
    assert runs_needed(off) == 0


def test_needs_event_log_lifecycle():
    assert needs_event_log_lifecycle(default_config()) is True
    off = parse("[checks.spark-ui-no-lifecycle]\nenabled = false")
    assert needs_event_log_lifecycle(off) is False


def test_spark_ui_check_takes_no_parameters():
    with pytest.raises(ConfigError, match="max_days"):
        parse("[checks.spark-ui-no-lifecycle]\nmax_days = 30")


def test_spark_ui_check_severity_can_be_overridden():
    cfg = parse('[checks.spark-ui-no-lifecycle]\nseverity = "info"')
    assert cfg.checks["spark-ui-no-lifecycle"].severity is Severity.INFO


def test_catalog_defaults_and_selection():
    cfg = default_config()
    assert cfg.catalog_include == ("*",) and cfg.catalog_exclude == ()
    cfg = parse('[catalog]\ninclude = ["prod_*"]\nexclude = ["prod_tmp*"]')
    assert cfg.database_selected("prod_sales")
    assert not cfg.database_selected("prod_tmp1")
    assert not cfg.database_selected("dev_sales")


@pytest.mark.parametrize(
    "text, fragment",
    [
        ("[catalog]\nincl = []", "incl"),
        ("[catalog]\ninclude = []", "catalog"),
        ('[catalog]\ninclude = [""]', "catalog"),
        ('[catalog]\nexclude = [" "]', "catalog"),
        ('catalog = "x"', "catalog"),
    ],
)
def test_invalid_catalog_section(text, fragment):
    with pytest.raises(ConfigError, match=fragment):
        parse(text)


def test_needs_catalog_and_min_failures():
    from glue_doctor.config import needs_catalog, optimizer_min_failures

    assert needs_catalog(default_config()) is True
    assert optimizer_min_failures(default_config()) == 2
    off = parse("[checks.iceberg-optimizer-suspended]\nenabled = false")
    assert needs_catalog(off) is False
    custom = parse("[checks.iceberg-optimizer-suspended]\nmin_failures = 4\nstale_days = 3")
    assert optimizer_min_failures(custom) == 4
    assert custom.checks["iceberg-optimizer-suspended"].params.stale_days == 3


@pytest.mark.parametrize(
    "body, fragment",
    [
        ("min_failures = 0", "min_failures"),
        ("stale_days = 0", "stale_days"),
        ("bogus = 1", "bogus"),
    ],
)
def test_optimizer_check_parameters_are_validated(body, fragment):
    with pytest.raises(ConfigError, match=fragment):
        parse(f"[checks.iceberg-optimizer-suspended]\n{body}")
