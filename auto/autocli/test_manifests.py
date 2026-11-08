"""Checks on the bundled manifests in auto/k3s."""

import glob
import os

import yaml

K3S_DIR = os.path.join(os.path.dirname(__file__), "..", "k3s")


def _persistent_volumes():
    for path in glob.glob(os.path.join(K3S_DIR, "**", "*.yaml"), recursive=True):
        with open(path, encoding="utf-8") as manifest:
            for doc in yaml.safe_load_all(manifest):
                if isinstance(doc, dict) and doc.get("kind") == "PersistentVolume":
                    yield path, doc


def test_service_volumes_have_their_own_directory_and_node():
    """No two services share a host directory, and every volume is pinned.

    mysql, minio, redis and matomo all used /mnt/data with no node pinning, so
    after a restart MySQL could be scheduled onto the node holding minio's
    buckets, find a non-empty directory, and refuse to initialize.
    """
    seen = {}
    for path, doc in _persistent_volumes():
        host_path = doc["spec"].get("hostPath", {}).get("path")
        if not host_path or host_path == "/mnt/code":
            continue  # the shared code mount is meant to be shared
        assert (
            host_path not in seen
        ), f"{path} reuses {host_path} from {seen.get(host_path)}"
        seen[host_path] = path
        terms = doc["spec"]["nodeAffinity"]["required"]["nodeSelectorTerms"]
        assert terms[0]["matchExpressions"][0]["values"] == [
            "k3d-k3s-default-server-0"
        ], path
    assert len(seen) >= 4  # mysql, postgres, redis, minio


def test_docker_hub_mirror_points_at_the_registry_container_port():
    """The nodes reach auto's registry on 5000; the host's 12345 refuses them."""
    with open(os.path.join(K3S_DIR, "registries.yaml"), encoding="utf-8") as config:
        mirrors = yaml.safe_load(config)["mirrors"]
    assert mirrors["docker.io"]["endpoint"] == ["http://k3d-registry.local:5000"]


def test_envoy_proxy_image_is_named_by_tag():
    """A digest-pinned image can never be served by the docker.io mirror."""
    with open(
        os.path.join(K3S_DIR, "envoy-gateway", "base.yaml"), encoding="utf-8"
    ) as manifest:
        proxy = next(
            doc
            for doc in yaml.safe_load_all(manifest)
            if doc and doc["kind"] == "EnvoyProxy"
        )
    image = proxy["spec"]["provider"]["kubernetes"]["envoyDeployment"]["container"][
        "image"
    ]
    assert image.startswith("docker.io/envoyproxy/envoy:")
    assert "@" not in image
