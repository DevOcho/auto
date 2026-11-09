"""Tests for auto.autocli.registry: building pod images and caching external ones.

The build tests came from this failure: a `docker build` died partway, but the
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
    with (
        patch.object(registry.progress, "build_image", return_value=0),
        patch.object(registry.progress, "push_image", return_value=1) as push,
        patch.object(registry.utils, "run_and_wait", return_value=1) as run,
    ):
        with pytest.raises(SystemExit) as exited:
            registry.tag_pod_docker_image(pod_repo)

    assert exited.value.code == 1
    assert not any(cmd.startswith("docker tag") for cmd in _commands(run))
    push.assert_not_called()


def test_failed_tag_stops_before_push(pod_repo):
    """Without the registry tag there is nothing correct to push"""
    with (
        patch.object(registry.progress, "build_image", return_value=1),
        patch.object(registry.progress, "push_image", return_value=1) as push,
        patch.object(registry.utils, "run_and_wait", return_value=0),
    ):
        with pytest.raises(SystemExit) as exited:
            registry.tag_pod_docker_image(pod_repo)

    assert exited.value.code == 1
    push.assert_not_called()


def test_failed_push_is_fatal(pod_repo):
    """The registry would keep serving the previous image, so this is fatal too"""
    with (
        patch.object(registry.progress, "build_image", return_value=1),
        patch.object(registry.progress, "push_image", return_value=0),
        patch.object(registry.utils, "run_and_wait", return_value=1) as run,
    ):
        with pytest.raises(SystemExit) as exited:
            registry.tag_pod_docker_image(pod_repo)

    assert exited.value.code == 1
    assert "docker image prune -f" not in _commands(run)


def test_successful_build_tags_pushes_and_cleans_up(pod_repo):
    """The happy path still builds, tags, pushes and prunes, in that order"""
    with (
        patch.object(registry.progress, "build_image", return_value=1) as build,
        patch.object(registry.progress, "push_image", return_value=1) as push,
        patch.object(registry.utils, "run_and_wait", return_value=1) as run,
    ):
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
    with (
        patch("autocli.registry._scan_namespaces_for_images", return_value=set()),
        patch(
            "autocli.registry._scan_nodes_for_sandbox_images",
            return_value={"docker.io/rancher/mirrored-pause:3.10.2"},
        ),
        patch("autocli.registry._get_registry_catalog", return_value=[]),
        patch("autocli.registry._cache_single_image") as cache,
        patch("autocli.registry.add_images_to_local_config") as add,
    ):
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

    with (
        patch.object(registry.utils, "run_and_wait", side_effect=run_and_wait),
        patch.object(registry.progress, "pull_image", return_value=0),
        patch.object(registry.progress, "push_image", return_value=push_ok) as push,
    ):
        registry._load_single_image(image)

    out = capsys.readouterr().out
    assert expected in out
    assert ("Loaded" in out) == (expected == "Loaded")
    assert push.called == bool(tag_ok)


@pytest.mark.parametrize(
    "image, repo",
    [
        ("minio/minio:RELEASE.2025-09-07T16-13-09Z", "minio/minio"),
        (
            "registry.k8s.io/ingress-nginx/controller:v1.15.1",
            "registry.k8s.io/ingress-nginx/controller",
        ),
        (
            "registry.k8s.io/ingress-nginx/kube-webhook-certgen:v1.6.4",
            "registry.k8s.io/ingress-nginx/kube-webhook-certgen",
        ),
        ("matomo:5.3.1-fpm-alpine", "matomo"),
    ],
)
def test_retired_image_is_skipped_with_a_note(capsys, image, repo):
    """An old local.yaml lists images 0.9 never uses; skip each with a note"""
    images = [{"image": image}, {"image": "floci/floci:2.1.0"}]
    assert registry._filter_images_to_load(images, loaded_repos=[]) == [images[1]]
    assert f"Skipping {repo}" in capsys.readouterr().out


def _system_pod(name, image, active=False, tmp_path=None):
    manifest = tmp_path / f"{name}.yaml"
    manifest.write_text(
        f"kind: Deployment\nspec:\n  template:\n    spec:\n      containers:\n"
        f"      - name: {name}\n        image: k3d-registry.local:12345/{image}\n",
        encoding="utf-8",
    )
    return {
        "pod": {
            "name": name,
            "active": active,
            "commands": [f"kubectl apply -f {manifest}"],
        }
    }


@pytest.mark.parametrize("required", [set(), {"mysql"}, {"minio"}])
def test_images_of_system_pods_that_are_off_wait(tmp_path, capsys, required):
    """A new machine needn't download a database nobody runs; it loads when one starts"""
    system_pods = [
        _system_pod("mysql", "mysql:8.0", tmp_path=tmp_path),
        _system_pod("floci-aws", "floci/floci:2.1.0", tmp_path=tmp_path),
    ]
    images = [
        {"image": "mysql:8.0"},
        {"image": "floci/floci:2.1.0"},
        {"image": "gitea/gitea:1.22"},
    ]
    loaded = []
    with (
        patch.dict(
            registry.CONFIG,
            {"registry": images, "system-pods": system_pods, "pods": []},
        ),
        patch("autocli.utils.get_required_system_pods", return_value=set(required)),
        patch("autocli.registry._get_registry_catalog", return_value=[]),
        patch(
            "autocli.registry._load_single_image",
            side_effect=lambda i: loaded.append(i["image"]),
        ),
        patch("autocli.registry._build_and_load_pods"),
    ):
        registry.populate_registry()
    expected = {"gitea/gitea:1.22"}  # no system pod's image: always loaded
    if "mysql" in required:
        expected.add("mysql:8.0")
    if "minio" in required:  # served by floci-aws
        expected.add("floci/floci:2.1.0")
    assert set(loaded) == expected
    waiting = {"mysql:8.0", "floci/floci:2.1.0"} - expected
    out = capsys.readouterr().out
    assert ("Not downloading yet" in out) == bool(waiting)
    assert all(image in out for image in waiting)


def test_load_missing_images_loads_only_what_the_registry_lacks():
    """Starting a system pod loads its images if they were skipped earlier"""
    loaded = []
    with (
        patch("autocli.registry._get_registry_catalog", return_value=["mysql"]),
        patch(
            "autocli.registry._load_single_image",
            side_effect=lambda i: loaded.append(i["image"]),
        ),
    ):
        registry.load_missing_images({"mysql:8.0", "postgres:18.1-trixie"})
    assert loaded == ["postgres:18.1-trixie"]


def test_load_missing_images_with_nothing_to_load_asks_nothing():
    """A system pod with no images of its own doesn't query the registry"""
    with patch("autocli.registry._get_registry_catalog") as catalog:
        registry.load_missing_images(set())
    catalog.assert_not_called()


@pytest.mark.parametrize(
    "image, repo",
    [
        ("mysql:8.0", "mysql"),
        ("rancher/mirrored-pause:3.10.2", "rancher/mirrored-pause"),
        ("registry.example.com:5000/team/app", "registry.example.com:5000/team/app"),
        (
            "registry.example.com:5000/team/app:1.4",
            "registry.example.com:5000/team/app",
        ),
    ],
)
def test_repo_of_a_registry_port_is_not_a_tag(image, repo):
    """The repository part of an image name"""
    assert registry._repo_of(image) == repo


def test_only_replaced_versions_of_k3s_and_envoy_images_are_stale():
    """Old versions of images auto chooses go; anything a pod might pin stays"""
    running = {
        "docker.io/rancher/mirrored-coredns-coredns:1.14.7",
        "docker.io/envoyproxy/envoy:distroless-v1.39.5",
        "mysql:8.0",
    }
    listed = [
        "rancher/mirrored-coredns-coredns:1.10.1",  # replaced: stale
        "docker.io/rancher/mirrored-coredns-coredns:1.12.0",  # replaced: stale
        "rancher/mirrored-coredns-coredns:1.14.7",  # running
        "envoyproxy/envoy:distroless-v1.38.0",  # replaced: stale
        "rancher/mirrored-library-traefik:2.10.7",  # not running at all: kept
        "mysql:5.7",  # not one auto chooses: kept
    ]
    with patch.dict(registry.CONFIG, {"registry": [{"image": i} for i in listed]}):
        assert registry._stale_managed_images(running) == [
            listed[0],
            listed[1],
            listed[3],
        ]


def test_caching_drops_replaced_k3s_versions_from_local_yaml(monkeypatch):
    """A new machine shouldn't download every coredns version auto ever saw"""
    monkeypatch.setitem(
        registry.CONFIG,
        "registry",
        [
            {"image": "rancher/mirrored-pause:3.6"},
            {"image": "rancher/mirrored-pause:3.10.2"},
        ],
    )
    with (
        patch("autocli.registry._scan_namespaces_for_images", return_value=set()),
        patch(
            "autocli.registry._scan_nodes_for_sandbox_images",
            return_value={"docker.io/rancher/mirrored-pause:3.10.2"},
        ),
        patch(
            "autocli.registry._get_registry_catalog",
            return_value=["rancher/mirrored-pause"],
        ),
        patch("autocli.registry._is_image_in_catalog", return_value=True),
        patch("autocli.registry.remove_images_from_local_config") as remove,
    ):
        registry.cache_running_images()
    remove.assert_called_once_with(["rancher/mirrored-pause:3.6"])


def test_images_are_cached_at_once_and_each_reported(capsys):
    """Several downloads run together; a failure is named, not fatal"""
    pairs = [(f"img{i}:1", f"img{i}:1") for i in range(6)]
    with patch(
        "autocli.registry._cache_single_image",
        side_effect=lambda full, clean: clean != "img3:1",
    ) as cache:
        registry._cache_images(pairs)
    assert cache.call_count == 6
    out = capsys.readouterr().out
    assert all(f"Cached: img{i}:1" in out for i in (0, 1, 2, 4, 5))
    assert "Could not cache img3:1" in out


@pytest.mark.parametrize("fails", ["inspect+pull", "tag", "push", None])
def test_caching_one_image_reports_whether_it_worked(fails):
    """No progress bars (they would overlap); the result is the return value"""

    def run(cmd, **_kw):
        if fails == "inspect+pull" and ("inspect" in cmd or "pull" in cmd):
            return False
        return not (fails and cmd.startswith(f"docker {fails}"))

    with patch("autocli.registry.utils.run_and_wait", side_effect=run):
        assert registry._cache_single_image("a:1", "a:1") is (fails is None)
