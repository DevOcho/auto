"""Container Registry and Image Management"""

import concurrent.futures
import os
import re
import time

import requests
import yaml
from requests.exceptions import RequestException
from rich import print as rprint

from autocli import progress, utils
from autocli.config import (
    CONFIG,
    add_images_to_local_config,
    remove_images_from_local_config,
)


def start_registry():
    """Start a container registry"""

    # Do we have a registry or do we need to create one?
    bash_command = """k3d registry list"""
    if not utils.run_and_wait(bash_command, check_result="k3d-registry.local"):
        # No registry found so we need to make one
        rprint(" -- Creating new registry")
        bash_command = (
            f"k3d registry create registry.local --port {utils.bind_address()}:12345"
        )
        utils.run_and_wait(bash_command)
        rprint("    [steel_blue1]Created Registry")
        time.sleep(3)
    else:
        # The nodes reach it inside Docker's network, so recreating it also
        # means recreating the cluster that was connected to the old one.
        utils.warn_if_bound_elsewhere(
            "k3d-registry.local",
            "The image registry",
            "auto stop --delete-cluster, k3d registry delete k3d-registry.local, "
            "then auto start",
        )


def _get_registry_catalog():
    """Safely fetch the catalog from the local registry."""
    try:
        req = requests.get("http://k3d-registry.local:12345/v2/_catalog", timeout=30)
        req.raise_for_status()
        return req.json().get("repositories", [])
    except RequestException:
        return []


# Images auto no longer uses that an old local.yaml still lists under
# `registry:`. minio/minio can't even be pulled any more (MinIO stopped
# publishing it; floci-aws serves S3 instead), so trying would only print an
# error on every start. Envoy Gateway replaced ingress-nginx in 0.9, and the
# matomo system pod is gone, so caching those only costs time and disk.
RETIRED_IMAGES = {
    "minio/minio",
    "registry.k8s.io/ingress-nginx/controller",
    "registry.k8s.io/ingress-nginx/kube-webhook-certgen",
    "matomo",
}


def _filter_images_to_load(registry_load_list, loaded_repos):
    """Filter out images that are already in the local registry."""
    filtered_list = []
    for image_obj in registry_load_list:
        if not isinstance(image_obj, dict) or "image" not in image_obj:
            continue

        full_image = image_obj["image"]
        clean_image = full_image.split("@")[0]
        repo_name = clean_image.split(":")[0] if ":" in clean_image else clean_image

        if repo_name in RETIRED_IMAGES:
            rprint(
                f"  -- [yellow]Skipping {repo_name}[/]: auto no longer uses it. "
                "Remove it from registry: in ~/.auto/config/local.yaml"
            )
            continue

        needs_load = True
        for image in loaded_repos:
            if re.search(re.escape(repo_name), image):
                needs_load = False
                break

        if needs_load:
            filtered_list.append(image_obj)
    return filtered_list


def _load_single_image(image_obj):
    """Pull, tag, and push a single image to the local k3d registry."""
    full_image = image_obj["image"]
    clean_image = full_image.split("@")[0]

    bash_cmd = "docker images"
    if not utils.run_and_wait(bash_cmd, check_result=clean_image, suppress_error=True):
        progress.pull_image(full_image)

    # A failed pull is not the end: a copy Docker already holds still tags and
    # pushes, which keeps an image working after its upstream repo disappears
    # (minio/minio left Docker Hub). The tag is what says whether we have one.
    tag_cmd = f"docker tag {full_image} k3d-registry.local:12345/{clean_image}"
    if not utils.run_and_wait(tag_cmd, capture_output=True, suppress_error=True):
        rprint(
            f"  -- [red]Not loaded: {clean_image}[/] could not be pulled and isn't on this "
            "machine. Pods that use it won't start."
        )
        return

    if not progress.push_image(
        f"k3d-registry.local:12345/{clean_image}", suppress_error=True
    ):
        rprint(
            f"  -- [red]Not loaded: {clean_image}[/] could not be pushed to the local registry."
        )
        return
    rprint(f"  -- Loaded: [bright_cyan]{clean_image}[/]")


def _build_and_load_pods(loaded_repos):
    """Build and load local pod repositories."""
    for pod in CONFIG.get("pods", []):
        skip_version = False

        if isinstance(pod, dict):
            pod_name = pod["repo"].split("/")[-1:][0].replace(".git", "")
        else:
            pod_name = pod

        if pod_name in loaded_repos:
            try:
                url = f"http://k3d-registry.local:12345/v2/{pod_name}/tags/list"
                req = requests.get(url, timeout=30)
                req.raise_for_status()
                image_info = req.json()

                pod_config_path = os.path.join(
                    CONFIG["code"], pod_name, ".auto", "config.yaml"
                )
                if os.path.isfile(pod_config_path):
                    with open(pod_config_path, encoding="utf-8") as pod_config_yaml:
                        pod_config = yaml.safe_load(pod_config_yaml)
                    version = pod_config.get("version", "latest")

                    tags = image_info.get("tags", [])
                    if tags and version in tags:
                        skip_version = True
            except RequestException:
                pass

            if skip_version:
                continue

        tag_pod_docker_image(pod_name)


def _images_only_off_system_pods_use():
    """Images that only the system pods this start won't run use"""
    required = utils.get_required_system_pods(CONFIG)
    # minio is served by floci-aws (services.REPLACED_SYSTEM_PODS)
    if "minio" in required:
        required.add("floci-aws")
    used, unused = set(), set()
    for sys_pod in CONFIG.get("system-pods", []):
        pod = sys_pod.get("pod", {})
        images = utils.manifest_images(pod.get("commands", []))
        (used if pod.get("name") in required else unused).update(images)
    return unused - used


def populate_registry():
    """Load important images into the registry to speed up deployment."""
    registry_load_list = list(CONFIG.get("registry", []))
    loaded_repos = _get_registry_catalog()

    filtered_load_list = _filter_images_to_load(registry_load_list, loaded_repos)

    # A system pod that is off doesn't need its images yet; each loads when its
    # pod starts (load_missing_images). On a new machine with the default
    # config, that skips about 1.5 minutes of downloads.
    deferred = _images_only_off_system_pods_use()
    later = [
        i for i in filtered_load_list if utils.plain_image_name(i["image"]) in deferred
    ]
    if later:
        rprint(
            "  -- Not downloading yet, as their system pods are off: "
            + ", ".join(i["image"] for i in later)
        )

    for image_obj in filtered_load_list:
        if image_obj not in later:
            _load_single_image(image_obj)

    _build_and_load_pods(loaded_repos)


def load_missing_images(images):
    """Load any of these images the local registry doesn't have yet"""
    if not images:
        return
    wanted = [{"image": image} for image in sorted(images)]
    for image_obj in _filter_images_to_load(wanted, _get_registry_catalog()):
        _load_single_image(image_obj)


def _scan_namespaces_for_images(namespaces):
    """Scan specified namespaces and return a set of all unique images running."""
    found_images = set()
    for ns in namespaces:
        cmd = (
            f"kubectl get pods -n {ns} "
            "-o jsonpath='{range .items[*]}{.spec.containers[*].image} "
            '{.spec.initContainers[*].image}{"\\n"}{end}\''
        )
        output = utils.run_and_return(cmd)
        if output:
            for image in output.split():
                if image.strip():
                    found_images.add(image.strip())
    return found_images


def _get_local_pod_names():
    """Get a list of local pod names defined in the configuration."""
    local_pod_names = []
    for p in CONFIG.get("pods", []):
        if isinstance(p, dict) and "repo" in p:
            name = p["repo"].split("/")[-1:][0].replace(".git", "")
            local_pod_names.append(name)
        elif isinstance(p, str):
            local_pod_names.append(p)
    return local_pod_names


def _scan_nodes_for_sandbox_images():
    """Return the pause image(s) the nodes run every pod's sandbox with.

    It is in no pod spec, so the namespace scan never sees it, but every pod
    waits on it; the nodes list it among their images. Its version follows the
    k3s version, so it is read from the cluster rather than pinned.
    """
    cmd = "kubectl get nodes -o jsonpath='{.items[*].status.images[*].names[*]}'"
    output = utils.run_and_return(cmd)
    return {name for name in output.split() if "/mirrored-pause:" in name}


def _filter_external_images(found_images, local_pod_names):
    """Filter out local images from the running cluster images.

    An explicit docker.io/ is dropped so a Docker Hub image is cached under the
    path the nodes' docker.io mirror asks for (envoyproxy/envoy, not
    docker.io/envoyproxy/envoy); see auto/k3s/registries.yaml.
    """
    images_to_process = set()
    for img in found_images:
        clean_image = img.replace("k3d-registry.local:12345/", "")
        clean_image = clean_image.removeprefix("docker.io/")

        is_local = False
        for pod_name in local_pod_names:
            if clean_image == pod_name or clean_image.startswith(f"{pod_name}:"):
                is_local = True
                break

        if not is_local:
            images_to_process.add(clean_image)
    return images_to_process


def _is_image_in_catalog(repo_name, tag_name, registry_catalog):
    """Check if a specific repo and tag combination is already mirrored."""
    if repo_name in registry_catalog:
        try:
            url = f"http://k3d-registry.local:12345/v2/{repo_name}/tags/list"
            tag_req = requests.get(url, timeout=30)
            tag_req.raise_for_status()
            tag_list = tag_req.json().get("tags", [])
            if tag_list and tag_name in tag_list:
                return True
        except RequestException:
            pass
    return False


def _cache_single_image(full_image, clean_image):
    """Pull and push a single image to the cache registry; True when it worked.

    Quiet, as several run at once (cache_running_images) and their progress
    bars would draw over each other.
    """

    def run(command):
        return utils.run_and_wait(command, capture_output=True, suppress_error=True)

    if not run(f"docker image inspect {clean_image}") and not run(
        f"docker pull {full_image}"
    ):
        return False
    if not run(f"docker tag {full_image} k3d-registry.local:12345/{clean_image}"):
        return False
    return bool(run(f"docker push k3d-registry.local:12345/{clean_image}"))


# Images caching runs at once. On a new machine there are about 7, and one at a
# time they took 68 s.
CACHE_WORKERS = 4

# Images whose versions auto chooses: k3s's own and Envoy Gateway's. Once the
# cluster runs a newer version of one, an older one left in registry: only
# costs every new machine a download.
AUTO_MANAGED_IMAGE_PREFIXES = ("rancher/", "envoyproxy/")


def _repo_of(image):
    """mysql for mysql:8.0; a registry's port is not a tag"""
    name, _, tag = image.rpartition(":")
    return name if name and "/" not in tag else image


def _stale_managed_images(running_images):
    """registry: entries for an older version of an image auto manages, now replaced"""
    running = {utils.plain_image_name(image) for image in running_images}
    running_repos = {_repo_of(image) for image in running}
    stale = []
    for item in CONFIG.get("registry") or []:
        if not isinstance(item, dict) or "image" not in item:
            continue
        plain = utils.plain_image_name(item["image"])
        if (
            plain.startswith(AUTO_MANAGED_IMAGE_PREFIXES)
            and _repo_of(plain) in running_repos
            and plain not in running
        ):
            stale.append(item["image"])
    return stale


def _cache_images(to_cache):
    """Cache (full_image, clean_image) pairs, several at once"""
    rprint(f"     = Caching {len(to_cache)} images, {CACHE_WORKERS} at a time")
    with concurrent.futures.ThreadPoolExecutor(max_workers=CACHE_WORKERS) as pool:
        futures = {pool.submit(_cache_single_image, f, c): c for f, c in to_cache}
        for future in concurrent.futures.as_completed(futures):
            try:
                cached = future.result()
            except Exception:  # noqa: BLE001
                cached = False
            if cached:
                rprint(f"     =[green]Cached:[/green] {futures[future]}")
            else:
                rprint(
                    f"     =[yellow]Could not cache[/] {futures[future]}: "
                    "the next start downloads it again"
                )


def cache_running_images():
    """Scan running pods for images and auto-add them to local registry and local.yaml silently."""
    rprint(
        "  -- Scanning default, kube-system, and envoy-gateway-system for external images..."
    )

    namespaces = ["default", "kube-system", "envoy-gateway-system"]
    found_images = _scan_namespaces_for_images(namespaces)
    found_images |= _scan_nodes_for_sandbox_images()
    local_pod_names = _get_local_pod_names()
    images_to_process = _filter_external_images(found_images, local_pod_names)

    stale = _stale_managed_images(found_images)
    if stale:
        rprint(
            "     = Dropping older k3s and Envoy versions from registry: "
            + ", ".join(stale)
        )
        remove_images_from_local_config(stale)

    if not images_to_process:
        return

    registry_list = CONFIG.get("registry", []) or []
    existing_config_images = {
        item["image"].split("@")[0]
        for item in registry_list
        if isinstance(item, dict) and "image" in item
    }

    new_images_for_config = set()
    registry_catalog = _get_registry_catalog()
    to_cache = []

    for full_image in sorted(images_to_process):
        clean_image = full_image.split("@")[0]
        repo_name = clean_image.split(":")[0] if ":" in clean_image else clean_image
        tag_name = clean_image.split(":")[1] if ":" in clean_image else "latest"

        is_mirrored = _is_image_in_catalog(repo_name, tag_name, registry_catalog)

        if clean_image not in existing_config_images:
            new_images_for_config.add(clean_image)

        if not is_mirrored:
            to_cache.append((full_image, clean_image))

    if to_cache:
        _cache_images(to_cache)

    if new_images_for_config:
        add_images_to_local_config(list(new_images_for_config))


def list_cluster_images():
    """Scan running pods and print a YAML list of images for local.yaml"""
    rprint("  -- Scanning cluster for images...")

    # 1. Get images from all containers and initContainers
    # We use a set to automatically handle deduplication
    found_images = set()

    # JSONPath to grab both standard containers and init containers
    cmd = (
        "kubectl get pods --all-namespaces "
        "-o jsonpath='{range .items[*]}{.spec.containers[*].image} "
        '{.spec.initContainers[*].image}{"\\n"}{end}\''
    )

    output = utils.run_and_return(cmd)
    if not output:
        rprint("     [yellow]No images found (is the cluster running?)[/]")
        return

    # 2. Process the images
    # We need to filter out the user's local pods (portal, www, etc) because
    # those shouldn't be pulled from a registry, they are built locally.
    local_pod_names = _get_local_pod_names()
    raw_list = output.split()
    for image in raw_list:
        # Strip the local registry prefix if present (e.g. k3d-registry.local:12345/mysql:8.0 -> mysql:8.0)
        clean_image = image.replace("k3d-registry.local:12345/", "")

        # Filter out local pods (naive check: if image starts with pod name)
        is_local_project = False
        for pod_name in local_pod_names:
            # Check if image is exactly the pod name or pod_name:tag
            if clean_image == pod_name or clean_image.startswith(f"{pod_name}:"):
                is_local_project = True
                break
        if not is_local_project:
            found_images.add(clean_image)

    # 3. Print the result
    rprint(f"     [green]Found {len(found_images)} unique upstream images.[/]")
    rprint("\n[bold]Copy this into your ~/.auto/config/local.yaml:[/bold]\n")

    print("registry:")
    for img in sorted(found_images):
        print(f"  - image: {img}")
    print()


def tag_pod_docker_image(pod) -> bool:
    """Tag and push a new docker image to local registry (True if it was pushed)"""

    # Local vars
    code_path = CONFIG["code"]

    # Verify the pod is real using the users source code folder.  Check before
    # reading its config so a typo gets this message, not a FileNotFoundError.
    if not os.path.isdir(os.path.join(code_path, pod)):
        print("")
        rprint(f"[red bold]ERROR: Portal {pod} does not exist")
        return False

    # We need to load the pod's config and see what version we are on
    pod_config_path = os.path.join(code_path, pod, ".auto", "config.yaml")
    with open(pod_config_path, encoding="utf-8") as pod_config_yaml:
        pod_config = yaml.safe_load(pod_config_yaml)
    version = pod_config["version"]

    rprint(f"  -- Building and Tagging: [bright_cyan]{pod} {version}")
    rprint(f"     = Found pod {pod}")

    # Perform docker build.  This streams a progress bar with an ETA based
    # on how long each step took the last few times we built this pod.
    #
    # A failed build has to stop everything here.  Docker still holds the last
    # good {pod}:{version} image under the same tag, so carrying on would tag
    # and push that stale image, the cluster would run it, and the command
    # would report success while the new code never arrived.
    if not progress.build_image(pod, version, f"{code_path}/{pod}"):
        utils.declare_error(
            f"Building [bright_cyan]{pod} {version}[/] failed (the docker output is above). "
            "Nothing was pushed, so anything already running keeps its old image. "
            "Fix the build and run this again."
        )
        return False

    # Tag the image for the registry (instant, so no progress bar)
    rprint(f"     = Tagging [bright_cyan]{pod}[/] image for the registry")
    command = f"docker tag {pod}:{version} k3d-registry.local:12345/{pod}:{version}"
    if not utils.run_and_wait(command):
        utils.declare_error(
            f"Could not tag the [bright_cyan]{pod}[/] image for the registry."
        )
        return False

    # Push the image to the registry.  Same reasoning as the build: if this
    # fails, the registry still serves the previous image under this tag.
    if not progress.push_image(f"k3d-registry.local:12345/{pod}:{version}"):
        utils.declare_error(
            f"Pushing [bright_cyan]{pod} {version}[/] to the local registry failed, "
            "so the cluster would still pull the previous image. Nothing was deployed."
        )
        return False

    # clean up your mess
    rprint("  -- Cleaning unused images")
    command = "docker image prune -f"
    utils.run_and_wait(command)

    return True
