# glue-doctor

[![Tests](https://github.com/vinpereira/glue-doctor/actions/workflows/test.yml/badge.svg)](https://github.com/vinpereira/glue-doctor/actions/workflows/test.yml)
[![Lint](https://github.com/vinpereira/glue-doctor/actions/workflows/lint.yml/badge.svg)](https://github.com/vinpereira/glue-doctor/actions/workflows/lint.yml)
[![PyPI](https://img.shields.io/pypi/v/glue-doctor)](https://pypi.org/project/glue-doctor/)
[![Python versions](https://img.shields.io/pypi/pyversions/glue-doctor)](https://pypi.org/project/glue-doctor/)
[![License](https://img.shields.io/pypi/l/glue-doctor)](https://github.com/vinpereira/glue-doctor/blob/main/LICENSE)

Audit CLI for AWS Glue jobs. It reads job definitions, run history, the S3 lifecycle rules of
Spark UI event logs and the Data Catalog's Iceberg table optimizers (read-only), reports
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
┃ Severity ┃ Resource   ┃ Check                ┃ Message                                                             ┃
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
2 finding(s) in 34 job(s) and 78 table(s)
```

## Checks

| ID | Default severity | What it detects |
|---|---|---|
| `timeout-too-generous` | warning | Timeout far above the median runtime of recent successful runs, once it exceeds a minimum (2 h by default; short timeouts are a sensible safety net). With little history, a timeout above an absolute cap |
| `timeout-too-tight` | warning | Recent runs take most of the timeout (p95 above 80% of it) |
| `glue-version` | warning below 5.0, info below 5.1 | Glue version older than the configured minimum. Glue 5.1 and 6.0 are supported |
| `observability-off` | info | `--enable-metrics` absent, job run insights explicitly disabled, or continuous logging off (only checked on Glue 4.0 and earlier; from 5.0 every job has real-time logging) |
| `spark-ui-no-lifecycle` | warning | Spark UI event logs in S3 that no enabled, expiring lifecycle rule covers, so they grow without bound. Rules narrower than the logs location and rules filtered by tag or size do not count |
| `iceberg-optimizer-suspended` | warning | An Iceberg table optimizer (compaction, snapshot retention, orphan file deletion) whose last runs keep failing, or a retention or orphan-file-deletion optimizer with no run for more than 7 days. It never claims the optimizer was suspended (the Glue API has no such state). Compaction is threshold driven, so "no run" is not reported for it |
| `collect-error` | warning | A resource's data (a job, a table or the catalog) could not be read, so the audit of it is incomplete |

Streaming jobs are skipped by the timeout checks; Python shell jobs are skipped by the version,
observability and Spark UI checks. `glue-doctor audit --help` lists every check.

Known limits of `spark-ui-no-lifecycle`:

- A rule counts as expiring when it has an `Expiration` by days or date. On a bucket with
  versioning enabled that only adds delete markers: the data stays as noncurrent versions unless
  the rule also sets `NoncurrentVersionExpiration`, which the check does not inspect (it would need
  `s3:GetBucketVersioning`).
- An `Expiration` date far in the future still counts as expiring.
- A bucket in another region is followed through S3's redirect. If the region cannot be resolved
  without an extra `HeadBucket` call (which the tool never makes), that bucket is reported as a
  `collect-error` with the code `BucketRegionUnresolved`, and the rest of the audit goes on.
- Reports contain the event-log location of each job (bucket and prefix). Glue's default event-log
  bucket name includes the account ID, so do not paste unredacted reports in public places.

Known limits of `iceberg-optimizer-suspended`:

- The failure rule was designed from the documented API and tested with synthetic data; no real
  failing optimizer has been observed yet.
- Tables that Lake Formation hides from the role are not listed, so they are not audited.
- Only optimizers configured on the table are read; an optimizer that is not configured is not an
  error.

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

[catalog]
include = ["*"]              # database globs scanned for Iceberg tables
exclude = ["scratch_*"]

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

[checks.iceberg-optimizer-suspended]
min_failures = 2             # consecutive failed runs needed to report
stale_days   = 7             # retention / orphan-file-deletion: days without a run

[[suppress]]
check    = "timeout-too-generous"
resource = "nightly-*"               # glob over the resource name: a job, or database.table
reason   = "Highly variable runtime" # required
until    = 2027-01-31                # optional; after this date the finding returns
```

Any check accepts `enabled` and `severity` (`error`, `warning` or `info`); a severity override
applies to every finding of that check. The region is read from `AWS_DEFAULT_REGION` when neither
the flag nor the file sets it.

## Permissions

`glue-doctor` calls `glue:GetJobs`, `glue:GetJobRuns`, `glue:GetDatabases`, `glue:GetTables`,
`glue:BatchGetTableOptimizer`, `glue:ListTableOptimizerRuns` and `s3:GetLifecycleConfiguration`
(plus `sts:GetCallerIdentity`, which needs no permission). The S3 call reads the lifecycle
configuration of each bucket that holds Spark UI event logs; the tool never lists or reads objects.
A minimal policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "glue:GetJobs",
        "glue:GetJobRuns",
        "glue:GetDatabases",
        "glue:GetTables",
        "glue:BatchGetTableOptimizer",
        "glue:ListTableOptimizerRuns"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": "s3:GetLifecycleConfiguration",
      "Resource": "arn:aws:s3:::example-event-logs-bucket"
    }
  ]
}
```

If you do not want to grant the S3 permission, disable the check; no S3 call is then made:

```toml
[checks.spark-ui-no-lifecycle]
enabled = false
```

To skip the catalog scan set `[checks.iceberg-optimizer-suspended] enabled = false`; no catalog call
is then made. Without the four Glue permissions and with the check enabled, the catalog is reported
as a `collect-error` (resource type `catalog`).

Without the permission and with the check enabled, every job with Spark UI enabled is reported as a
`collect-error`, because a failed read must never look like a clean audit. S3 directory buckets
are not supported and are reported the same way. Any other AWS operation is blocked inside the
tool. Only Glue's own special job arguments are read from a job definition; your custom arguments
(which can hold secrets) never enter a report.

## JSON output

`--format json` prints a document validated by `glue_doctor/schemas/report-v2.json`
(`schema_version: 2`) on stdout; warnings and errors go to stderr. Findings carry `resource_type`
(`job`, `table` or `catalog`) and `resource` (a job name, `database.table`, or `catalog` / a
database name). Version 0.3.0 renamed the former `job` field to `resource`.

## Development

```bash
make sync    # install dependencies
make check   # lint + format + test
```

## License

Apache-2.0.
