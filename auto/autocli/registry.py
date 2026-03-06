"""Container Registry and Image Management"""

import os
import re
import time

import requests
import yaml
from autocli import utils
from autocli.config import CONFIG, add_images_to_local_config
from requests.exceptions import RequestException
from rich import print as rprint


def start_registry():
    """Start a container registry"""

    # Do we have a registry or do we need to create one?
    bash_command = """/usr/local/bin/k3d registry list"""
    if not utils.run_and_wait(bash_command, check_result="k3d-registry.local"):
        # No registry found so we need to make one
        rprint(" -- Creating new registry")
        bash_command = """k3d registry create registry.local --port 12345"""
        utils.run_and_wait(bash_command)
        rprint("    [steel_blue1]Created Registry")
        time.sleep(3)


def _get_registry_catalog():
    """Safely fetch the catalog from the local registry."""
    try:
        req = requests.get("http://k3d-registry.local:12345/v2/_catalog", timeout=30)
        req.raise_for_status()
        return req.json().get("repositories", [])
    except RequestException:
        return []


def _filter_images_to_load(registry_load_list, loaded_repos):
    """Filter out images that are already in the local registry."""
    filtered_list = []
    for image_obj in registry_load_list:
        if not isinstance(image_obj, dict) or "image" not in image_obj:
            continue

        full_image = image_obj["image"]
        clean_image = full_image.split("@")[0]
        repo_name = clean_image.split(":")[0] if ":" in clean_image else clean_image

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
        utils.run_and_wait(f"docker pull {full_image}", capture_output=True)

    tag_cmd = f"docker tag {full_image} k3d-registry.local:12345/{clean_image}"
    utils.run_and_wait(tag_cmd, capture_output=True, suppress_error=True)

    push_cmd = f"docker push k3d-registry.local:12345/{clean_image}"
    utils.run_and_wait(push_cmd, capture_output=True, suppress_error=True)
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


def populate_registry():
    """Load important images into the registry to speed up deployment."""
    registry_load_list = list(CONFIG.get("registry", []))
    loaded_repos = _get_registry_catalog()

    filtered_load_list = _filter_images_to_load(registry_load_list, loaded_repos)

    for image_obj in filtered_load_list:
        _load_single_image(image_obj)

    _build_and_load_pods(loaded_repos)


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


def _filter_external_images(found_images, local_pod_names):
    """Filter out local images from the running cluster images."""
    images_to_process = set()
    for img in found_images:
        clean_image = img.replace("k3d-registry.local:12345/", "")

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
    """Pull and push a single image to the cache registry."""
    rprint(f"     =[green]Caching external image:[/green] {clean_image}")

    inspect_cmd = f"docker image inspect {clean_image}"
    if not utils.run_and_wait(inspect_cmd, capture_output=True, suppress_error=True):
        utils.run_and_wait(
            f"docker pull {full_image}", capture_output=True, suppress_error=True
        )

    tag_cmd = f"docker tag {full_image} k3d-registry.local:12345/{clean_image}"
    utils.run_and_wait(tag_cmd, capture_output=True, suppress_error=True)

    push_cmd = f"docker push k3d-registry.local:12345/{clean_image}"
    utils.run_and_wait(push_cmd, capture_output=True, suppress_error=True)


def cache_running_images():
    """Scan running pods for images and auto-add them to local registry and local.yaml silently."""
    rprint(
        "  -- Scanning default, kube-system, and ingress-nginx for external images..."
    )

    namespaces = ["default", "kube-system", "ingress-nginx"]
    found_images = _scan_namespaces_for_images(namespaces)
    local_pod_names = _get_local_pod_names()
    images_to_process = _filter_external_images(found_images, local_pod_names)

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

    for full_image in sorted(images_to_process):
        clean_image = full_image.split("@")[0]
        repo_name = clean_image.split(":")[0] if ":" in clean_image else clean_image
        tag_name = clean_image.split(":")[1] if ":" in clean_image else "latest"

        is_mirrored = _is_image_in_catalog(repo_name, tag_name, registry_catalog)

        if clean_image not in existing_config_images:
            new_images_for_config.add(clean_image)

        if not is_mirrored:
            _cache_single_image(full_image, clean_image)

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


def tag_pod_docker_image(pod) -> None:
    """Tag and push a new docker image to local registry"""

    # Local vars
    code_path = CONFIG["code"]

    # We need to load the pod's config and see what version we are on
    pod_config_path = os.path.join(code_path, pod, ".auto", "config.yaml")
    with open(pod_config_path, encoding="utf-8") as pod_config_yaml:
        pod_config = yaml.safe_load(pod_config_yaml)
    version = pod_config["version"]

    rprint(f"  -- Building and Tagging: [bright_cyan]{pod} {version}")

    # Verify the pod is real using the users source code folder
    if os.path.isdir(os.path.join(code_path, pod)):
        rprint(f"     = Found pod {pod}")

        # Perform docker build
        rprint(f"     = Building [bright_cyan]{pod}[/] container")
        command = f"docker build -t {pod}:{version} {code_path}/{pod}"
        utils.run_and_wait(command)

        # Tag the image for the registry
        rprint(f"     = Tagging [bright_cyan]{pod}[/] image for the registry")
        command = f"docker tag {pod}:{version} k3d-registry.local:12345/{pod}:{version}"
        utils.run_and_wait(command)

        # Push the image to the registry
        rprint(f"     = Pushing [bright_cyan]{pod}[/] image to the registry")
        command = f"docker push k3d-registry.local:12345/{pod}:{version}"
        utils.run_and_wait(command)

        # clean up your mess
        rprint("  -- Cleaning unused images")
        command = "docker image prune -f"
        utils.run_and_wait(command)

    # They tried to build a pod that didn't exist.  Maybe a typo?
    else:
        print("")
        rprint(f"[red bold]ERROR: Portal {pod} does not exist")
