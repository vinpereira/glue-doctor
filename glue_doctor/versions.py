"""Knowledge about Glue versions, kept as data so new releases are a one-line change."""

# (Spark, Python) per known Glue version, from the Glue documentation.
_RUNTIMES: dict[tuple[int, int], tuple[str, str]] = {
    (3, 0): ("3.1.1", "3.7"),
    (4, 0): ("3.3.0", "3.10"),
    (5, 0): ("3.5.4", "3.11"),
    (5, 1): ("3.5.6", "3.11"),
    (6, 0): ("4.1.1", "3.13"),
}

LATEST_KNOWN = (6, 0)

_LEGACY_DEFAULT_TIMEOUT_MIN = 2880  # Glue 4.0 and earlier
_MODERN_DEFAULT_TIMEOUT_MIN = 480  # Glue 5.0 and later


def parse_glue_version(raw: str | None) -> tuple[int, int] | None:
    """Parse "5.1" into (5, 1). Unknown or malformed values return None."""
    if not raw:
        return None
    parts = raw.strip().split(".")
    try:
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        return None
    return (major, minor)


def format_version(version: tuple[int, int]) -> str:
    return f"{version[0]}.{version[1]}"


def runtime_of(version: tuple[int, int]) -> tuple[str, str] | None:
    return _RUNTIMES.get(version)


def resolve_timeout(explicit: int | None, version: tuple[int, int] | None) -> tuple[int, str]:
    """Return the effective timeout in minutes and where it came from."""
    if explicit is not None:
        return explicit, "explicit"
    if version is None:
        return _LEGACY_DEFAULT_TIMEOUT_MIN, "assumed-default"
    if version >= (5, 0):
        return _MODERN_DEFAULT_TIMEOUT_MIN, "default"
    return _LEGACY_DEFAULT_TIMEOUT_MIN, "default"
