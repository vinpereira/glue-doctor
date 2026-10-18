# Roadmap

What is planned after v0.3.0. Nothing here is a commitment or has a date, and check IDs are working
names until released.

Each item keeps three kinds of statements apart:

- **Verified in the AWS documentation**: stated by the AWS documentation (checked in October 2026).
- **Observed**: seen once in a read-only spike against a real account. Useful, but not a documented
  guarantee.
- **To validate**: an idea or an open question that must be settled before the check is built.

## Next

### `missing-failure-alarm`

Idea: report Glue jobs whose failures nobody hears about.

**Verified in the AWS documentation**

- Glue sends `Glue Job State Change` events to EventBridge for `SUCCEEDED`, `FAILED`, `TIMEOUT` and
  `STOPPED`. The event detail includes `jobName`, `state`, `severity`, `jobRunId` and `message`.
- Glue job metrics are available in CloudWatch and alarms can be set on any of them. The scenarios
  the documentation gives are memory, straggling executors and data backlog.

**To validate**

- What counts as coverage: a CloudWatch alarm, an EventBridge rule on `FAILED` / `TIMEOUT`, or both.
- How a rule is matched to a job: an explicit `jobName` list, a prefix, or one account-wide rule.
  EventBridge patterns are rich; start with a small supported subset and report the rest as
  `collect-error`.
- Whether a rule without targets counts.
- False positives for jobs orchestrated by Step Functions or Glue workflows, whose failure alarm
  lives elsewhere. The lesson of `timeout-too-generous` applies: calibrate on a real account first.
- The permissions to add to the allowlist (`cloudwatch:DescribeAlarms`, `events:ListRules`,
  `events:ListTargetsByRule`, to be confirmed in the AWS documentation).

Starting with a spike on a real account.

## Known gaps in released checks

All of these are ideas to validate, not decisions.

- `spark-ui-no-lifecycle`
  - A versioned bucket needs `NoncurrentVersionExpiration` too; detecting it needs
    `s3:GetBucketVersioning`.
  - An `Expiration` date far in the future counts as expiring.
  - Directory buckets are not supported (the AWS documentation lists a different permission and
    endpoint for them) and are reported as a `collect-error`.
  - `s3a://` and `s3n://` paths are ignored; confirm whether Glue accepts them.
  - A bucket whose region cannot be resolved without a `HeadBucket` call is reported as
    `BucketRegionUnresolved`. Widen the allowlist only if real accounts hit this often.
- `iceberg-optimizer-suspended`
  - The failure rule has no real failing optimizer to confirm it yet; whether a service-side stop
    ever shows up as `enabled = false`, or only as failed runs, is still to validate.
  - Optimizers configured at the catalog level (without a table-level entry) are not read.
  - Tables hidden by Lake Formation are not listed, so they are not audited.
- Timeout checks use the job's current timeout. In a real account about one run in five carried a
  different timeout, mostly from timeouts changed over time; per-run timeouts are not used.

## Later

- Worker type and count suggestions from job metrics (without hardcoding worker types).
- Job bookmarks enabled but unused (needs a reliable signal first).
- Version checks for Python shell and Ray jobs.
- Multi-region and multi-account runs; optional `GLUE_DOCTOR_*` environment variables.
- A pack of curated CloudWatch Logs Insights queries for Glue.

## Done

- 0.3.0: `iceberg-optimizer-suspended` (failing runs, and daily optimizers that stopped; compaction is
  never reported for "no run"), `[catalog]` filters, the `resource` rename (report schema 2).
- 0.2.0: `spark-ui-no-lifecycle`.
- 0.1.1: locked dependencies in CI, `NO_COLOR` and guard tests, run paging fix.
- 0.1.0: `timeout-too-generous`, `timeout-too-tight`, `glue-version`, `observability-off`,
  `collect-error`.
