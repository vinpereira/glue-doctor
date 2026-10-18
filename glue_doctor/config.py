import fnmatch
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from datetime import date
from pathlib import Path
from typing import Any

from glue_doctor.checks import REGISTRY
from glue_doctor.models import Severity

FAIL_ON_CHOICES = ("error", "warning", "info", "never")
DEFAULT_CONFIG_NAME = "glue-doctor.toml"

_TOP_KEYS = {"fail_on", "region", "profile", "jobs", "catalog", "checks", "suppress"}
_SEVERITIES = [s.value for s in Severity]


class ConfigError(Exception):
    """Raised for any invalid configuration (maps to exit code 2)."""


@dataclass(frozen=True)
class Suppression:
    check: str
    resource: str
    reason: str
    until: date | None


@dataclass(frozen=True)
class CheckSettings:
    enabled: bool
    severity: Severity | None
    params: Any


@dataclass(frozen=True)
class Config:
    fail_on: str = "warning"
    region: str | None = None
    profile: str | None = None
    include: tuple[str, ...] = ("*",)
    exclude: tuple[str, ...] = ()
    catalog_include: tuple[str, ...] = ("*",)
    catalog_exclude: tuple[str, ...] = ()
    checks: dict[str, CheckSettings] = field(default_factory=dict)
    suppressions: tuple[Suppression, ...] = ()

    def job_selected(self, name: str) -> bool:
        included = any(fnmatch.fnmatchcase(name, p) for p in self.include)
        excluded = any(fnmatch.fnmatchcase(name, p) for p in self.exclude)
        return included and not excluded

    def database_selected(self, name: str) -> bool:
        included = any(fnmatch.fnmatchcase(name, p) for p in self.catalog_include)
        excluded = any(fnmatch.fnmatchcase(name, p) for p in self.catalog_exclude)
        return included and not excluded


def _default_checks() -> dict[str, CheckSettings]:
    return {
        check_id: CheckSettings(enabled=True, severity=None, params=spec.params_cls())
        for check_id, spec in REGISTRY.items()
    }


def default_config() -> Config:
    return Config(checks=_default_checks())


def _string_list(where: str, value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{where}: expected a list of strings")
    return tuple(value)


def _glob_list(where: str, value: Any, *, allow_empty_list: bool) -> tuple[str, ...]:
    patterns = _string_list(where, value)
    if not patterns and not allow_empty_list:
        raise ConfigError(f"{where}: must not be empty (omit the key to audit every job)")
    if not all(p.strip() for p in patterns):
        raise ConfigError(f"{where}: patterns must not be empty")
    return patterns


def _optional_string(data: Mapping[str, Any], key: str) -> str | None:
    value = data.get(key)
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ConfigError(f"{key}: expected a non-empty string")
    return value


def _coerce(where: str, value: Any, expected: Any) -> Any:
    if expected is bool:
        if isinstance(value, bool):
            return value
    elif expected is int:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    elif expected is float:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    elif expected is str:
        if isinstance(value, str):
            return value
    elif expected == tuple[str, ...]:
        return _string_list(where, value)
    names = {bool: "a boolean", int: "an integer", float: "a number", str: "a string"}
    raise ConfigError(f"{where}: expected {names.get(expected, 'a valid value')}")


def _parse_globs(section: str, raw: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if not isinstance(raw, Mapping):
        raise ConfigError(f"[{section}]: expected a table")
    unknown = set(raw) - {"include", "exclude"}
    if unknown:
        raise ConfigError(f"[{section}]: unknown key(s): {', '.join(sorted(unknown))}")
    include = ("*",)
    if "include" in raw:
        include = _glob_list(f"[{section}].include", raw["include"], allow_empty_list=False)
    exclude = ()
    if "exclude" in raw:
        exclude = _glob_list(f"[{section}].exclude", raw["exclude"], allow_empty_list=True)
    return include, exclude


def _parse_checks(raw: Any) -> dict[str, CheckSettings]:
    if not isinstance(raw, Mapping):
        raise ConfigError("[checks]: expected a table")
    checks = _default_checks()
    for check_id, body in raw.items():
        if check_id not in REGISTRY:
            raise ConfigError(f"[checks.{check_id}]: unknown check; known: {', '.join(REGISTRY)}")
        if not isinstance(body, Mapping):
            raise ConfigError(f"[checks.{check_id}]: expected a table")
        spec = REGISTRY[check_id]
        param_types = {f.name: f.type for f in fields(spec.params_cls)}
        allowed = {"enabled", "severity", *param_types}
        unknown = set(body) - allowed
        if unknown:
            raise ConfigError(
                f"[checks.{check_id}]: unknown key(s): {', '.join(sorted(unknown))}; "
                f"allowed: {', '.join(sorted(allowed))}"
            )
        enabled = _coerce(f"[checks.{check_id}].enabled", body.get("enabled", True), bool)
        severity = None
        if "severity" in body:
            if body["severity"] not in _SEVERITIES:
                raise ConfigError(
                    f"[checks.{check_id}].severity: must be one of {', '.join(_SEVERITIES)}"
                )
            severity = Severity(body["severity"])
        kwargs = {
            key: _coerce(f"[checks.{check_id}].{key}", value, param_types[key])
            for key, value in body.items()
            if key in param_types
        }
        try:
            params = spec.params_cls(**kwargs)
        except ValueError as exc:
            raise ConfigError(f"[checks.{check_id}]: {exc}") from exc
        checks[check_id] = CheckSettings(enabled=enabled, severity=severity, params=params)
    return checks


def _parse_suppressions(raw: Any) -> tuple[Suppression, ...]:
    if not isinstance(raw, list):
        raise ConfigError("suppress: expected an array of tables ([[suppress]])")
    result = []
    for index, item in enumerate(raw, start=1):
        where = f"[[suppress]] #{index}"
        if not isinstance(item, Mapping):
            raise ConfigError(f"{where}: expected a table")
        allowed = {"check", "resource", "reason", "until"}
        unknown = set(item) - allowed
        if unknown:
            hint = " ('job' was renamed to 'resource' in 0.3.0)" if "job" in unknown else ""
            raise ConfigError(
                f"{where}: unknown key(s): {', '.join(sorted(unknown))}{hint}; "
                f"allowed: {', '.join(sorted(allowed))}"
            )
        check = item.get("check")
        if not isinstance(check, str) or check not in REGISTRY:
            raise ConfigError(f"{where}: unknown check {check!r}")
        reason = item.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ConfigError(f"{where}: 'reason' is required and must not be empty")
        resource = item.get("resource", "*")
        if not isinstance(resource, str) or not resource.strip():
            raise ConfigError(f"{where}: 'resource' must be a non-empty string glob")
        until = item.get("until")
        if until is not None and type(until) is not date:
            raise ConfigError(f"{where}: 'until' must be a TOML date (YYYY-MM-DD)")
        result.append(
            Suppression(check=check, resource=resource, reason=reason.strip(), until=until)
        )
    return tuple(result)


def parse_config(data: Mapping[str, Any]) -> Config:
    unknown = set(data) - _TOP_KEYS
    if unknown:
        raise ConfigError(f"unknown key(s): {', '.join(sorted(unknown))}")
    fail_on = data.get("fail_on", "warning")
    if fail_on not in FAIL_ON_CHOICES:
        raise ConfigError(f"fail_on: must be one of {', '.join(FAIL_ON_CHOICES)}")
    include, exclude = _parse_globs("jobs", data.get("jobs", {}))
    catalog_include, catalog_exclude = _parse_globs("catalog", data.get("catalog", {}))
    return Config(
        fail_on=fail_on,
        region=_optional_string(data, "region"),
        profile=_optional_string(data, "profile"),
        include=include,
        exclude=exclude,
        catalog_include=catalog_include,
        catalog_exclude=catalog_exclude,
        checks=_parse_checks(data.get("checks", {})),
        suppressions=_parse_suppressions(data.get("suppress", [])),
    )


def load_config(path: Path | None) -> Config:
    if path is None:
        return default_config()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"cannot read config file {path}: {type(exc).__name__}") from exc
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc
    return parse_config(data)


def discover_config_path(explicit: Path | None, cwd: Path | None = None) -> Path | None:
    if explicit is not None:
        return explicit
    candidate = (cwd or Path.cwd()) / DEFAULT_CONFIG_NAME
    return candidate if candidate.is_file() else None


def runs_needed(config: Config) -> int:
    """How many runs per job the collector must fetch (0 means do not call GetJobRuns)."""
    wanted = [
        settings.params.history
        for check_id, settings in config.checks.items()
        if settings.enabled and REGISTRY[check_id].needs_runs
    ]
    return max(wanted, default=0)


def needs_event_log_lifecycle(config: Config) -> bool:
    """Whether the collector must read S3 lifecycle configurations (an enabled check needs them)."""
    return any(
        settings.enabled and REGISTRY[check_id].needs_event_log_lifecycle
        for check_id, settings in config.checks.items()
    )
