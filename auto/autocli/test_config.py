"""Tests for auto.autocli.config"""

import os
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


@pytest.mark.parametrize(
    "argv, needed",
    [
        (["--version"], False),
        (["--help"], False),
        (["start", "-h"], False),
        ([], False),  # bare `auto` prints help
        (["start"], True),
        (["logs", "shop"], True),
    ],
)
def test_only_real_commands_need_the_code_folder(argv, needed):
    """`auto --version` and help must not ask to create the code folder"""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("_AUTO_COMPLETE", None)
        assert config._needs_code_folder(argv) is needed


def test_tab_completion_never_asks_for_the_code_folder():
    """Completing a word must never stop to ask a question"""
    with patch.dict(os.environ, {"_AUTO_COMPLETE": "bash_complete"}):
        assert config._needs_code_folder(["start"]) is False


def _config_pointing_at_a_missing_folder(tmp_path):
    config_dir = tmp_path / ".auto" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "local.yaml").write_text(
        f"code: {tmp_path / 'missing'}\n", encoding="utf-8"
    )


def test_version_with_a_missing_code_folder_asks_nothing(tmp_path):
    """`auto --version` on a fresh machine prints the version and asks nothing"""
    _config_pointing_at_a_missing_folder(tmp_path)
    with (
        patch(
            "autocli.config.os.path.expanduser",
            side_effect=lambda p: p.replace("~", str(tmp_path)),
        ),
        patch("autocli.config.sys.argv", ["auto", "--version"]),
        patch("autocli.config.Confirm.ask") as ask,
    ):
        assert config.load_config()["code"] == str(tmp_path / "missing")
    ask.assert_not_called()


def test_missing_code_folder_without_a_terminal_says_what_to_do(tmp_path):
    """A script or CI has no one to answer the prompt: explain, don't traceback"""
    _config_pointing_at_a_missing_folder(tmp_path)
    with (
        patch(
            "autocli.config.os.path.expanduser",
            side_effect=lambda p: p.replace("~", str(tmp_path)),
        ),
        patch("autocli.config.sys.argv", ["auto", "start"]),
        patch("autocli.config.sys.stdin.isatty", return_value=False),
        patch("autocli.config.Confirm.ask") as ask,
        patch("autocli.config.rprint") as mock_print,
    ):
        with pytest.raises(SystemExit):
            config.load_config()
    ask.assert_not_called()
    message = " ".join(str(call.args[0]) for call in mock_print.call_args_list)
    assert "set code:" in message


def _add_images(tmp_path, text, images):
    """Run add_images_to_local_config on a local.yaml holding `text`"""
    config_dir = tmp_path / ".auto" / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / "local.yaml"
    path.write_text(text, encoding="utf-8")
    with (
        patch(
            "autocli.config.os.path.expanduser",
            side_effect=lambda p: p.replace("~", str(tmp_path)),
        ),
        patch.dict(config.CONFIG, {"registry": []}),
    ):
        config.add_images_to_local_config(images)
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("indent", ["", "  ", "    "])
def test_new_images_match_the_registry_lists_indent(tmp_path, indent):
    """A list written flush with its key is valid YAML; appending at another indent broke it"""
    text = f"code: /c\nregistry:\n{indent}- image: mysql:8.0\nsystem-pods: []\n"
    result = _add_images(tmp_path, text, ["redis:7.2-alpine"])
    assert f"{indent}- image: redis:7.2-alpine\n" in result
    assert yaml.safe_load(result)["registry"] == [
        {"image": "mysql:8.0"},
        {"image": "redis:7.2-alpine"},
    ]


def test_an_empty_flow_registry_becomes_a_list(tmp_path):
    """`registry: []` keeps its list on the key's line; turn it into a block list"""
    result = _add_images(tmp_path, "code: /c\nregistry: []\n", ["redis:7.2-alpine"])
    assert yaml.safe_load(result)["registry"] == [{"image": "redis:7.2-alpine"}]


def test_a_registry_layout_auto_cant_extend_is_left_alone(tmp_path):
    """Writing would leave local.yaml unreadable, so say what to add instead"""
    text = "code: /c\nregistry: [{image: mysql:8.0}]\n"
    with patch("autocli.config.rprint") as mock_print:
        result = _add_images(tmp_path, text, ["redis:7.2-alpine"])
    assert result == text
    assert "redis:7.2-alpine" in str(mock_print.call_args.args[0])


def test_removing_images_deletes_only_their_lines(tmp_path):
    """Stale k3s versions go; comments, other images and other keys stay as written"""
    text = (
        "code: /c\n"
        "registry:\n"
        "  # cached by auto\n"
        "  - image: rancher/mirrored-coredns-coredns:1.10.1\n"
        "  - image: mysql:8.0\n"
        "  - image: rancher/mirrored-coredns-coredns:1.14.7\n"
        "system-pods: []\n"
    )
    config_dir = tmp_path / ".auto" / "config"
    config_dir.mkdir(parents=True)
    path = config_dir / "local.yaml"
    path.write_text(text, encoding="utf-8")
    registry = [
        {"image": "rancher/mirrored-coredns-coredns:1.10.1"},
        {"image": "mysql:8.0"},
        {"image": "rancher/mirrored-coredns-coredns:1.14.7"},
    ]
    with (
        patch(
            "autocli.config.os.path.expanduser",
            side_effect=lambda p: p.replace("~", str(tmp_path)),
        ),
        patch.dict(config.CONFIG, {"registry": registry}),
    ):
        config.remove_images_from_local_config(
            ["rancher/mirrored-coredns-coredns:1.10.1"]
        )
        assert config.CONFIG["registry"] == registry[1:]
    assert path.read_text(encoding="utf-8") == text.replace(
        "  - image: rancher/mirrored-coredns-coredns:1.10.1\n", ""
    )
