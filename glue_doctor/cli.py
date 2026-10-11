from dataclasses import replace
from datetime import date
from pathlib import Path

import click

from glue_doctor import __version__
from glue_doctor.checks import REGISTRY
from glue_doctor.collector import AuditAborted, collect_snapshots, connect
from glue_doctor.config import (
    FAIL_ON_CHOICES,
    ConfigError,
    discover_config_path,
    load_config,
    runs_needed,
)
from glue_doctor.report import build_report, render_json, render_table
from glue_doctor.runner import exit_code_for, run_checks


def _checks_epilog() -> str:
    lines = ["\b", "Checks:"]
    for spec in REGISTRY.values():
        lines.append(f"  {spec.id:<22} [{spec.default_severity.value}] {spec.description}")
    return "\n".join(lines)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="glue-doctor")
def cli() -> None:
    """Audit AWS Glue jobs: timeouts, Glue versions and observability settings."""


@cli.command(epilog=_checks_epilog())
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path, dir_okay=False),
    help="TOML config file (default: ./glue-doctor.toml if present).",
)
@click.option("--region", help="AWS region (default: the boto3 credential chain).")
@click.option("--profile", help="AWS profile name.")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["table", "json"]),
    default="table",
    show_default=True,
    help="Report format.",
)
@click.option(
    "--fail-on",
    type=click.Choice(list(FAIL_ON_CHOICES)),
    help="Lowest severity that makes the exit code non-zero (default: warning).",
)
@click.option("--job", "jobs", multiple=True, metavar="GLOB", help="Only audit matching jobs.")
@click.pass_context
def audit(ctx, config_path, region, profile, fmt, fail_on, jobs):
    """Audit the AWS Glue jobs of one account and region (read-only).

    Exit codes: 0 no finding at or above --fail-on, 1 findings, 2 usage or config error,
    3 audit impossible (for example missing credentials) or internal error.
    """
    try:
        code = _run_audit(config_path, region, profile, fmt, fail_on, jobs)
    except (click.ClickException, click.exceptions.Exit):
        raise
    except Exception as exc:  # a crash must never look like "findings found" (exit 1)
        click.echo(
            f"Error: internal error: {type(exc).__name__}. "
            "Please report it at https://github.com/vinpereira/glue-doctor/issues",
            err=True,
        )
        code = 3
    ctx.exit(code)


def _run_audit(config_path, region, profile, fmt, fail_on, jobs) -> int:
    if any(not job.strip() for job in jobs):
        raise click.UsageError("--job must not be empty")
    try:
        config = load_config(discover_config_path(config_path))
    except ConfigError as exc:
        raise click.UsageError(str(exc)) from exc
    config = replace(
        config,
        fail_on=fail_on or config.fail_on,
        region=region or config.region,
        profile=profile or config.profile,
        include=tuple(jobs) or config.include,
    )
    try:
        aws = connect(config.profile, config.region)
        snapshots = collect_snapshots(aws.client, config.job_selected, runs_needed(config))
    except AuditAborted as exc:
        click.echo(f"Error: {exc}", err=True)
        return 3
    if not snapshots:
        click.echo(
            "Warning: no jobs were audited; check --job/include patterns and the region",
            err=True,
        )
    result = run_checks(snapshots, config, date.today())
    for warning in result.warnings:
        click.echo(f"Warning: {warning}", err=True)
    code = exit_code_for(result.findings, config.fail_on)
    report = build_report(result, region=aws.region, account_id=aws.account_id, exit_code=code)
    if fmt == "json":
        click.echo(render_json(report))
    else:
        render_table(report)
    return code
