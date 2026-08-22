"""Tests for auto.autocli.commands"""

import json
import os
from unittest.mock import patch

from autocli import commands
from click.testing import CliRunner


def _release_json(tag):
    """Minimal GitHub releases API payload with the given tag_name."""
    return json.dumps({"tag_name": tag})


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


# ---------------------------------------------------------------------------
# `auto update`
# ---------------------------------------------------------------------------


@patch("autocli.commands.os.system")
@patch("autocli.utils.run_and_return")
def test_update_already_latest(mock_run, mock_system):
    """`auto update` (no arg) is a no-op when already on the latest release."""
    mock_run.return_value = _release_json(f"v{commands.VERSION}")
    runner = CliRunner()
    result = runner.invoke(commands.update, [])
    assert result.exit_code == 0
    assert "already the latest" in result.output
    mock_system.assert_not_called()


@patch("autocli.commands.os.system")
@patch("autocli.utils.run_and_return")
def test_update_newer_available(mock_run, mock_system):
    """`auto update` runs the installer (no AUTO_VERSION) when behind."""
    mock_run.return_value = _release_json("v99.0.0")
    runner = CliRunner()
    result = runner.invoke(commands.update, [])
    assert result.exit_code == 0
    mock_system.assert_called_once_with(commands.INSTALLER)


@patch("autocli.commands.os.system")
@patch("autocli.utils.run_and_return")
def test_update_force_reinstalls_latest(mock_run, mock_system):
    """`auto update --force` reinstalls latest even when already current."""
    mock_run.return_value = _release_json(f"v{commands.VERSION}")
    runner = CliRunner()
    result = runner.invoke(commands.update, ["--force"])
    assert result.exit_code == 0
    mock_system.assert_called_once_with(commands.INSTALLER)


@patch("autocli.commands.os.system")
@patch("autocli.utils.run_and_return")
def test_update_latest_dry_run(mock_run, mock_system):
    """`auto update --dry-run` previews without running the installer."""
    mock_run.return_value = _release_json("v99.0.0")
    runner = CliRunner()
    result = runner.invoke(commands.update, ["--dry-run"])
    assert result.exit_code == 0
    assert "Dry run" in result.output
    mock_system.assert_not_called()


# ---------------------------------------------------------------------------
# `auto update <version>` — pinned / rollback path
# ---------------------------------------------------------------------------


@patch("autocli.commands.subprocess.run")
@patch("autocli.commands._release_http_status", return_value="200")
def test_update_pinned_version(mock_status, mock_run):
    """`auto update 0.7.1` installs with AUTO_VERSION set in the child env."""
    runner = CliRunner()
    result = runner.invoke(commands.update, ["0.7.1"])
    assert result.exit_code == 0
    mock_status.assert_called_once_with("0.7.1")
    mock_run.assert_called_once()
    args, kwargs = mock_run.call_args
    assert args[0] == ["bash", "-c", commands.INSTALLER]
    assert kwargs["env"]["AUTO_VERSION"] == "0.7.1"


@patch("autocli.commands.subprocess.run")
@patch("autocli.commands._release_http_status", return_value="200")
def test_update_pinned_strips_leading_v(mock_status, mock_run):
    """A leading 'v' is normalized before it reaches the API or installer."""
    runner = CliRunner()
    result = runner.invoke(commands.update, ["v0.7.1"])
    assert result.exit_code == 0
    mock_status.assert_called_once_with("0.7.1")
    _, kwargs = mock_run.call_args
    assert kwargs["env"]["AUTO_VERSION"] == "0.7.1"


@patch("autocli.commands.subprocess.run")
@patch("autocli.commands._release_http_status", return_value="200")
def test_update_pinned_dry_run(mock_status, mock_run):
    """`auto update 0.7.1 --dry-run` checks existence but runs nothing."""
    runner = CliRunner()
    result = runner.invoke(commands.update, ["0.7.1", "--dry-run"])
    assert result.exit_code == 0
    assert "Dry run" in result.output
    assert "AUTO_VERSION=0.7.1" in result.output
    mock_status.assert_called_once_with("0.7.1")
    mock_run.assert_not_called()


@patch("autocli.commands.subprocess.run")
def test_update_pinned_lookup_failures(mock_run):
    """Failed lookups (missing version, rate limit, no network) abort untouched."""
    runner = CliRunner()
    cases = [("404", "not found"), ("403", "rate limit"), ("", "connection")]
    for status, fragment in cases:
        with patch("autocli.commands._release_http_status", return_value=status):
            result = runner.invoke(commands.update, ["0.7.1"])
        assert result.exit_code != 0, status
        assert fragment in result.output.lower(), status
    mock_run.assert_not_called()


@patch("autocli.commands.subprocess.run")
@patch("autocli.commands._release_http_status")
def test_update_force_with_version_errors(mock_status, mock_run):
    """--force combined with a VERSION is a hard error, not a silent no-op."""
    runner = CliRunner()
    result = runner.invoke(commands.update, ["0.7.1", "--force"])
    assert result.exit_code != 0
    mock_status.assert_not_called()
    mock_run.assert_not_called()


@patch("autocli.commands.subprocess.run")
@patch("autocli.commands._release_http_status")
def test_update_rejects_injection(mock_status, mock_run):
    """Shell-metacharacter versions are rejected before any shell/network call."""
    runner = CliRunner()
    bad_versions = [
        "0.7.1; rm -rf ~",
        "$(reboot)",
        "0.7.1 | cat",
        "latest",
        "1.0.0-oops",
        "",
    ]
    for bad in bad_versions:
        result = runner.invoke(commands.update, [bad])
        assert result.exit_code != 0, bad
        assert "Invalid version" in result.output, bad
    mock_status.assert_not_called()
    mock_run.assert_not_called()


def test_install_script_supports_auto_version():
    """Guard against silent removal of AUTO_VERSION support in the installer."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "install_auto.sh"), encoding="utf-8") as handle:
        content = handle.read()
    assert "AUTO_VERSION" in content
    assert "releases/tags/v" in content


@patch("autocli.services.connect_to_mailpit")
def test_mailpit_command(mock_connect):
    """Test the mailpit command mapping to services"""
    runner = CliRunner()
    result = runner.invoke(commands.mailpit)
    assert result.exit_code == 0
    mock_connect.assert_called()
