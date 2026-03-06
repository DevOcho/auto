"""Tests for auto.autocli.commands"""

from unittest.mock import patch

from autocli import commands
from click.testing import CliRunner


def test_auto_help():
    """Test the auto command help"""
    runner = CliRunner()
    result = runner.invoke(commands.auto, ["--help"])
    assert result.exit_code == 0
    assert "Commandline utility" in result.output


@patch("autocli.core.bootstrap_cluster")
def test_start_full(mock_bootstrap):
    """Test the full start command flow routing"""
    runner = CliRunner()
    result = runner.invoke(commands.start)

    assert result.exit_code == 0
    mock_bootstrap.assert_called()


@patch("autocli.core.stop_cluster")
def test_stop_cluster(mock_stop):
    """Test the stop command"""
    runner = CliRunner()
    result = runner.invoke(commands.stop)

    assert result.exit_code == 0
    mock_stop.assert_called()


@patch("autocli.core.delete_cluster")
def test_delete_cluster(mock_delete):
    """Test the stop --delete-cluster command"""
    runner = CliRunner()
    result = runner.invoke(commands.stop, ["--delete-cluster"])

    assert result.exit_code == 0
    mock_delete.assert_called()


@patch("autocli.registry.list_cluster_images")
def test_images_command(mock_list):
    """Test the images command mapping to registry"""
    runner = CliRunner()
    result = runner.invoke(commands.images)
    assert result.exit_code == 0
    mock_list.assert_called()
