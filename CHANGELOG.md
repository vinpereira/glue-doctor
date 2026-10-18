# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.3.0] - 2026-10-18

### Breaking

- The report is now about resources, not only jobs. Each finding has `resource_type` (`job`,
  `table` or `catalog`) and `resource`; the former `job` field is gone. The JSON `schema_version`
  is 2 and the schema file is `glue_doctor/schemas/report-v2.json` (`report-v1.json` was removed).
  `summary` gains `tables_audited`.
- In `[[suppress]]` the key `job` is now `resource` (a glob over the resource name). An old config
  fails with exit code 2 and a message that names the new key. `--job` and `[jobs]` are unchanged.

### Added

- `iceberg-optimizer-suspended` check: reports Iceberg table optimizers (compaction, snapshot
  retention, orphan file deletion) whose last runs keep failing (`min_failures`, default 2) and
  retention or orphan-file-deletion optimizers with no run for more than `stale_days` (default 7).
- `[catalog] include` / `exclude`: database globs for the catalog scan.
- A catalog read failure is reported as a `collect-error` with `resource_type` `catalog` (the
  resource is `catalog` or the database name) and does not stop the audit of jobs.

### Changed

- The check needs four new Glue permissions: `glue:GetDatabases`, `glue:GetTables`,
  `glue:BatchGetTableOptimizer` and `glue:ListTableOptimizerRuns`. After upgrading without them the
  catalog is reported as a `collect-error` (exit code 1 by default). Grant the permissions, or set
  `[checks.iceberg-optimizer-suspended] enabled = false`; with the check disabled no catalog call
  is made.
- The `collect-error` hint now depends on the operation and error code: the S3 hint only for
  denials of the lifecycle read, a catalog hint for catalog denials, and a generic hint otherwise.
- The `collect-error` evidence includes `target` (the bucket, database or `database.table`) when
  the read that failed has one.

## [0.2.0] - 2026-10-15

### Added

- `spark-ui-no-lifecycle` check: reports Glue jobs whose Spark UI event logs in S3 are not covered
  by an enabled lifecycle rule that expires objects, so they grow without bound. A bucket whose
  region cannot be resolved is reported as a `collect-error` (`BucketRegionUnresolved`) instead of
  failing the audit.

### Changed

- The check needs the new IAM permission `s3:GetLifecycleConfiguration` on the event-log buckets.
  After upgrading without it, each job with Spark UI enabled is reported as a `collect-error`
  (exit code 1 by default). Grant the permission, or set
  `[checks.spark-ui-no-lifecycle] enabled = false`; with the check disabled no S3 call is made.
- The `collect-error` remediation now names the missing S3 permission and the way to disable the
  check when the denied operation is the lifecycle read.

## [0.1.1] - 2026-10-13

### Fixed

- Run collection no longer stops early when TIMEOUT runs crowd out successful ones, which could
  leave `timeout-too-generous` with too few runs to compare against.

### Changed

- CI and release workflows install dependencies with `--locked`, so a stale `uv.lock` fails the
  build instead of being re-resolved silently.
- Tests now cover colors on a real terminal with and without `NO_COLOR`, and the read-only guard
  on paginators and presigned URLs.

## [0.1.0] - 2026-10-13

### Added

- `glue-doctor audit`: read-only audit of the Glue jobs of one account and region.
- Checks: `timeout-too-generous`, `timeout-too-tight`, `glue-version` (Glue 5.1 and 6.0 supported),
  `observability-off`, `collect-error`.
- Optional TOML configuration with per-check parameters, severity overrides and suppressions with a
  mandatory `reason` and an optional `until` date.
- Table and JSON output (`schema_version: 1`) and exit codes 0/1/2/3 with `--fail-on`.
- Read-only guard: only `GetJobs` and `GetJobRuns` can be called on Glue; user-defined job
  arguments never enter a report.
- Release workflow: publishing is blocked unless the tag matches the package version and lint
  and tests pass.
