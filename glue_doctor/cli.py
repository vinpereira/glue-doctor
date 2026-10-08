import click

from glue_doctor import __version__


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="glue-doctor")
def cli() -> None:
    """Audit AWS Glue jobs: timeouts, Glue versions and observability settings."""
