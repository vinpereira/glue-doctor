import tomllib
from datetime import date

import pytest

from glue_doctor.checks import REGISTRY
from glue_doctor.config import (
    ConfigError,
    default_config,
    discover_config_path,
    load_config,
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
job = "legacy-*"
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
    assert (sup.check, sup.job, sup.reason, sup.until) == (
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
    assert cfg.suppressions[0].job == "*" and cfg.suppressions[0].until is None


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
        ('[[suppress]]\ncheck = "glue-version"\nreason = "x"\njob = ""', "job"),
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
