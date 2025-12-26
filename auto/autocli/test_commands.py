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


@patch("autocli.core.verify_dependencies")
@patch("autocli.core.start_cluster")
@patch("autocli.core.pull_and_build_pods")
@patch("autocli.core.start_registry")
@patch("autocli.core.populate_registry")
@patch("autocli.core.install_system_pods")
@patch("autocli.core.create_databases")
@patch("autocli.core.install_pods_in_cluster")
def test_start_full(
    mock_pods, mock_db, mock_sys, mock_pop, mock_reg, mock_build, mock_start, mock_deps
):  # pylint: disable=too-many-arguments, unused-argument
    """Test the full start command flow"""
    runner = CliRunner()
    result = runner.invoke(commands.start)

    assert result.exit_code == 0
    mock_deps.assert_called()
    mock_start.assert_called()
    mock_pods.assert_called()


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


@patch("autocli.core.list_cluster_images")
def test_images_command(mock_list):
    """Test the images command"""
    runner = CliRunner()
    result = runner.invoke(commands.images)
    assert result.exit_code == 0
    mock_list.assert_called()
