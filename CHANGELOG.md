# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.1.0] - 2026-10-11

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
