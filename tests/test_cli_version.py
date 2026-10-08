from click.testing import CliRunner

from glue_doctor import __version__
from glue_doctor.cli import cli


def test_version_flag():
    result = CliRunner().invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output
