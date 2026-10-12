# glue-doctor

[![Tests](https://github.com/vinpereira/glue-doctor/actions/workflows/test.yml/badge.svg)](https://github.com/vinpereira/glue-doctor/actions/workflows/test.yml)
[![Lint](https://github.com/vinpereira/glue-doctor/actions/workflows/lint.yml/badge.svg)](https://github.com/vinpereira/glue-doctor/actions/workflows/lint.yml)
[![PyPI](https://img.shields.io/pypi/v/glue-doctor)](https://pypi.org/project/glue-doctor/)
[![Python versions](https://img.shields.io/pypi/pyversions/glue-doctor)](https://pypi.org/project/glue-doctor/)
[![License](https://img.shields.io/pypi/l/glue-doctor)](https://github.com/vinpereira/glue-doctor/blob/main/LICENSE)

Audit CLI for AWS Glue jobs. It reads job definitions and run history (read-only), reports
configuration risks, and exits non-zero so it can gate CI.

## Install

```bash
uvx glue-doctor audit --region eu-central-1
# or
uv tool install glue-doctor
```

Requires Python 3.13 or newer.

## Quick start

```bash
glue-doctor audit --profile my-profile --region eu-central-1
glue-doctor audit --format json | jq '.summary'
glue-doctor audit --job "etl-*" --fail-on error
```

Example output:

```text
┏━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Severity ┃ Job        ┃ Check                ┃ Message                                                             ┃
┡━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ warning  │ etl-orders │ timeout-too-generous │ Timeout is 2880 min but the median successful run takes 12 min      │
│          │            │                      │ (240x)                                                              │
│          │            │                      │ Set the timeout closer to the observed runtime so a hung run is     │
│          │            │                      │ stopped early                                                       │
│ info     │ etl-legacy │ glue-version         │ Glue 4.0 (Spark 3.3.0, Python 3.10) is older than 5.1               │
│          │            │                      │ Upgrade to Glue 5.1 or newer. Glue 6.0 runs Spark 4 (ANSI SQL mode  │
│          │            │                      │ on by default) and costs less, so validate your job before          │
│          │            │                      │ migrating                                                           │
└──────────┴────────────┴──────────────────────┴─────────────────────────────────────────────────────────────────────┘
2 finding(s) in 34 job(s)
```

## Checks

| ID | Default severity | What it detects |
|---|---|---|
| `timeout-too-generous` | warning | Timeout far above the median runtime of recent successful runs, once it exceeds a minimum (2 h by default; short timeouts are a sensible safety net). With little history, a timeout above an absolute cap |
| `timeout-too-tight` | warning | Recent runs take most of the timeout (p95 above 80% of it) |
| `glue-version` | warning below 5.0, info below 5.1 | Glue version older than the configured minimum. Glue 5.1 and 6.0 are supported |
| `observability-off` | info | `--enable-metrics` absent, job run insights explicitly disabled, or continuous logging off (only checked on Glue 4.0 and earlier; from 5.0 every job has real-time logging) |
| `collect-error` | warning | A job's data could not be read, so the audit of that job is incomplete |

Streaming jobs are skipped by the timeout checks; Python shell jobs are skipped by the version and
observability checks. `glue-doctor audit --help` lists every check.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | No finding at or above `--fail-on` (default `warning`) |
| 1 | At least one finding at or above the threshold (including `collect-error`) |
| 2 | Usage or configuration error |
| 3 | Audit impossible (no credentials, `GetJobs` denied, no or invalid region) or an internal error |

## Configuration

Optional. `glue-doctor` reads `./glue-doctor.toml` (or `--config PATH`); flags override the file.
Unknown keys are errors, so a typo never silently disables a check.

```toml
fail_on = "warning"          # error | warning | info | never
region  = "eu-central-1"
profile = "my-profile"

[jobs]
include = ["*"]
exclude = ["scratch-*"]

[checks.timeout-too-generous]
severity    = "warning"
max_ratio   = 10.0           # timeout / median of successful runs
history     = 20             # last N successful runs
min_runs    = 5              # fewer runs: fall back to cap_minutes
cap_minutes = 1440
min_timeout_minutes = 120    # ignore timeouts up to this value, whatever the ratio

[checks.timeout-too-tight]
p95_ratio = 0.8

[checks.glue-version]
warn_below = "5.0"
info_below = "5.1"

[checks.observability-off]
enabled = false

[[suppress]]
check  = "timeout-too-generous"
job    = "nightly-*"
reason = "Highly variable runtime"   # required
until  = 2027-01-31                  # optional; after this date the finding returns
```

Any check accepts `enabled` and `severity` (`error`, `warning` or `info`); a severity override
applies to every finding of that check. The region is read from `AWS_DEFAULT_REGION` when neither
the flag nor the file sets it.

## Permissions

`glue-doctor` only calls `glue:GetJobs` and `glue:GetJobRuns` (plus `sts:GetCallerIdentity`, which
needs no permission). A minimal policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Action": ["glue:GetJobs", "glue:GetJobRuns"], "Resource": "*" }
  ]
}
```

Any other AWS operation is blocked inside the tool. Only Glue's own special job arguments are read
from a job definition; your custom arguments (which can hold secrets) never enter a report.

## JSON output

`--format json` prints a document validated by `glue_doctor/schemas/report-v1.json`
(`schema_version: 1`) on stdout; warnings and errors go to stderr.

## Development

```bash
make sync    # install dependencies
make check   # lint + format + test
```

## License

Apache-2.0.
