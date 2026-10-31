"""Tests for auto.autocli.registry.tag_pod_docker_image.

The failure that prompted these: a `docker build` died partway, but the
function ignored the result, tagged and pushed the stale image Docker still
held under the same tag, and the pod started on old code while `auto start`
exited 0. Each step now has to succeed before the next one runs.
"""

from unittest.mock import patch

import pytest
from autocli import registry


@pytest.fixture(name="pod_repo")
def fixture_pod_repo(tmp_path, monkeypatch):
    """A code folder holding one pod with a minimal .auto/config.yaml"""
    config_dir = tmp_path / "www-wagtail" / ".auto"
    config_dir.mkdir(parents=True)
    (config_dir / "config.yaml").write_text("name: www-wagtail\nversion: 0.1.0\n")
    monkeypatch.setitem(registry.CONFIG, "code", str(tmp_path))
    return "www-wagtail"


def _commands(run_and_wait):
    return [call.args[0] for call in run_and_wait.call_args_list]


def test_failed_build_stops_before_tag_and_push(pod_repo):
    """A failed build must never push the stale image or exit 0"""
    with patch.object(registry.progress, "build_image", return_value=0), patch.object(
        registry.progress, "push_image", return_value=1
    ) as push, patch.object(registry.utils, "run_and_wait", return_value=1) as run:
        with pytest.raises(SystemExit) as exited:
            registry.tag_pod_docker_image(pod_repo)

    assert exited.value.code == 1
    assert not any(cmd.startswith("docker tag") for cmd in _commands(run))
    push.assert_not_called()


def test_failed_tag_stops_before_push(pod_repo):
    """Without the registry tag there is nothing correct to push"""
    with patch.object(registry.progress, "build_image", return_value=1), patch.object(
        registry.progress, "push_image", return_value=1
    ) as push, patch.object(registry.utils, "run_and_wait", return_value=0):
        with pytest.raises(SystemExit) as exited:
            registry.tag_pod_docker_image(pod_repo)

    assert exited.value.code == 1
    push.assert_not_called()


def test_failed_push_is_fatal(pod_repo):
    """The registry would keep serving the previous image, so this is fatal too"""
    with patch.object(registry.progress, "build_image", return_value=1), patch.object(
        registry.progress, "push_image", return_value=0
    ), patch.object(registry.utils, "run_and_wait", return_value=1) as run:
        with pytest.raises(SystemExit) as exited:
            registry.tag_pod_docker_image(pod_repo)

    assert exited.value.code == 1
    assert "docker image prune -f" not in _commands(run)


def test_successful_build_tags_pushes_and_cleans_up(pod_repo):
    """The happy path still builds, tags, pushes and prunes, in that order"""
    with patch.object(
        registry.progress, "build_image", return_value=1
    ) as build, patch.object(
        registry.progress, "push_image", return_value=1
    ) as push, patch.object(
        registry.utils, "run_and_wait", return_value=1
    ) as run:
        assert registry.tag_pod_docker_image(pod_repo) is True

    build.assert_called_once()
    push.assert_called_once_with("k3d-registry.local:12345/www-wagtail:0.1.0")
    assert _commands(run) == [
        "docker tag www-wagtail:0.1.0 k3d-registry.local:12345/www-wagtail:0.1.0",
        "docker image prune -f",
    ]


def test_missing_pod_is_not_built(tmp_path, monkeypatch):
    """A typo'd pod name still gets the friendly message, not a build attempt"""
    monkeypatch.setitem(registry.CONFIG, "code", str(tmp_path))
    with patch.object(registry.progress, "build_image") as build:
        assert registry.tag_pod_docker_image("no-such-pod") is False
    build.assert_not_called()
