# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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
