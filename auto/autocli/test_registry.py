"""Tests for auto.autocli.registry: building pod images and caching external ones.

The build tests came from this failure: a `docker build` died partway, but the
function ignored the result, tagged and pushed the stale image Docker still
held under the same tag, and the pod started on old code while `auto start`
exited 0. Each step now has to succeed before the next one runs.
"""

# pylint: disable=protected-access

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


# The nodes' docker.io mirror (auto/k3s/registries.yaml) asks auto's registry for
# a Docker Hub image by its path on Docker Hub, so the cache has to store it there.


def test_docker_hub_images_are_cached_under_their_mirror_path():
    """docker.io/ is dropped; local pods are skipped; other names are kept."""
    found = {
        "docker.io/envoyproxy/envoy:distroless-v1.39.5@sha256:9d84",
        "envoyproxy/gateway:v1.9.3",
        "k3d-registry.local:12345/mysql:8.0",
        "k3d-registry.local:12345/kirkland:0.1.0",
        "rancher/mirrored-coredns-coredns:1.14.7",
    }
    assert registry._filter_external_images(found, ["kirkland"]) == {
        "envoyproxy/envoy:distroless-v1.39.5@sha256:9d84",
        "envoyproxy/gateway:v1.9.3",
        "mysql:8.0",
        "rancher/mirrored-coredns-coredns:1.14.7",
    }


def test_sandbox_image_is_read_from_the_nodes():
    """The pause image is in no pod spec; the nodes list it with a tag and a digest."""
    node_images = (
        "docker.io/rancher/mirrored-pause@sha256:74c4 docker.io/rancher/mirrored-pause:3.10.2 "
        "docker.io/rancher/mirrored-coredns-coredns:1.14.7"
    )
    with patch(
        "autocli.registry.utils.run_and_return", return_value=node_images
    ) as run:
        assert registry._scan_nodes_for_sandbox_images() == {
            "docker.io/rancher/mirrored-pause:3.10.2"
        }
    assert "kubectl get nodes" in run.call_args.args[0]


def test_running_images_cache_includes_the_pause_image(monkeypatch):
    """A new k3s version's pause image ends up in the registry for the next cluster."""
    monkeypatch.setitem(registry.CONFIG, "registry", [])
    with patch(
        "autocli.registry._scan_namespaces_for_images", return_value=set()
    ), patch(
        "autocli.registry._scan_nodes_for_sandbox_images",
        return_value={"docker.io/rancher/mirrored-pause:3.10.2"},
    ), patch(
        "autocli.registry._get_registry_catalog", return_value=[]
    ), patch(
        "autocli.registry._cache_single_image"
    ) as cache, patch(
        "autocli.registry.add_images_to_local_config"
    ) as add:
        registry.cache_running_images()
    cache.assert_called_once_with(
        "rancher/mirrored-pause:3.10.2", "rancher/mirrored-pause:3.10.2"
    )
    add.assert_called_once_with(["rancher/mirrored-pause:3.10.2"])


@pytest.mark.parametrize(
    "tag_ok, push_ok, expected",
    [(1, 1, "Loaded"), (0, 1, "isn't on this machine"), (1, 0, "could not be pushed")],
)
def test_external_image_is_reported_loaded_only_when_it_was(
    tag_ok, push_ok, expected, capsys
):
    """A pull that failed with no local copy used to print "Loaded" anyway"""
    image = {"image": "minio/minio:RELEASE.2025-09-07T16-13-09Z"}

    def run_and_wait(cmd, **_kwargs):
        if cmd.startswith("docker tag"):
            return tag_ok
        return 0  # `docker images` doesn't list it, so it is pulled

    with patch.object(
        registry.utils, "run_and_wait", side_effect=run_and_wait
    ), patch.object(registry.progress, "pull_image", return_value=0), patch.object(
        registry.progress, "push_image", return_value=push_ok
    ) as push:
        registry._load_single_image(image)

    out = capsys.readouterr().out
    assert expected in out
    assert ("Loaded" in out) == (expected == "Loaded")
    assert push.called == bool(tag_ok)
