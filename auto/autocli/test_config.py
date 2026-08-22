"""Tests for auto.autocli.config"""

# pylint: disable=protected-access

from unittest.mock import patch

import pytest
import yaml
from autocli import config


def _write_default_config(tmp_path):
    """Run create_initial_config() against a throwaway HOME and return the file"""
    with patch("autocli.config.os.path.expanduser", return_value=str(tmp_path)):
        config.create_initial_config()
    return tmp_path / ".auto" / "config" / "local.yaml"


def test_default_config_is_valid_yaml(tmp_path):
    """The generated local.yaml has to load back in -- load_config() reads it.

    A `commands:[` typo here (no space after the colon) makes the whole file a
    single plain scalar, and every `auto` command then dies with a YAMLError
    before it can do anything.
    """
    config_file = _write_default_config(tmp_path)

    parsed = yaml.safe_load(config_file.read_text(encoding="utf-8"))

    assert parsed["code"]
    assert parsed["pods"]
    assert parsed["system-pods"]


def test_default_config_system_pods_are_usable(tmp_path):
    """install_system_pods() reads pod.name and pod.commands off each entry."""
    config_file = _write_default_config(tmp_path)
    parsed = yaml.safe_load(config_file.read_text(encoding="utf-8"))

    names = []
    for entry in parsed["system-pods"]:
        pod = entry["pod"]
        assert isinstance(pod["commands"], list)
        assert all(cmd.startswith("kubectl apply") for cmd in pod["commands"])
        names.append(pod["name"])

    assert "mailpit" in names


def test_load_config_reports_bad_yaml_instead_of_raising(tmp_path):
    """A hand-edited, malformed local.yaml gets a CLI error, not a traceback."""
    config_dir = tmp_path / ".auto" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "local.yaml").write_text("pods: [oops\n", encoding="utf-8")

    with patch("autocli.config.os.path.expanduser", return_value=str(tmp_path)):
        with patch("autocli.config.rprint") as mock_print:
            with pytest.raises(SystemExit):
                config.load_config()

    message = " ".join(str(call.args[0]) for call in mock_print.call_args_list)
    assert "not valid YAML" in message


def test_load_config_treats_empty_file_as_empty_config(tmp_path):
    """safe_load returns None for an empty file; callers expect a dict."""
    config_dir = tmp_path / ".auto" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "local.yaml").write_text("", encoding="utf-8")

    with patch("autocli.config.os.path.expanduser", return_value=str(tmp_path)):
        assert config.load_config() == {}
