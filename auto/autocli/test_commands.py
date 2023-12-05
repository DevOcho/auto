""" Imports """
from autocli import commands
from click.testing import CliRunner


def test_auto():
    """Test the auto command function in auto"""
    runner = CliRunner()
    result = runner.invoke(commands.auto)
    assert result.exit_code == 0


def test_start():
    """Test the start command"""
    runner = CliRunner()
    result = runner.invoke(commands.start, ["--dry-run"])
    assert result.exit_code == 0
