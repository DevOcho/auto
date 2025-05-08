"""Imports"""
import os
import re
import shutil
import time
import json
import subprocess
import requests
import yaml
import shlex
from typing import Dict, List
from autocli import utils
from rich import print as rprint

CONFIG = utils.load_config()

def start_registry():
    """Start a container registry with error handling"""
    try:
        result = subprocess.run(
            ["k3d", "registry", "list", "-o", "json"],
            capture_output=True,
            text=True,
            check=True
        )
        registries = json.loads(result.stdout)
        
        if not any(r["name"] == "k3d-registry.local" for r in registries):
            rprint(" -- Creating new registry")
            subprocess.run(
                ["k3d", "registry", "create", "registry.local", "--port", "12345"],
                check=True
            )
            time.sleep(2)
            rprint("    [steel_blue1]Created Registry")
    except subprocess.CalledProcessError as e:
        utils.declare_error(f"Registry operation failed: {e.stderr}")

def populate_registry():
    """Load images into registry with validation"""
    registry_load_list = CONFIG.get("registry", [])
    req = None  # Initialize with default value
    
    # Attempt registry connection with retries
    for attempt in range(3):
        try:
            req = requests.get("http://k3d-registry.local:12345/v2/_catalog", timeout=10)
            if req.status_code == 200:
                break
        except requests.ConnectionError:
            if attempt == 2:
                utils.declare_error("Failed to connect to registry")
            time.sleep(2 * (attempt + 1))

    # Validate successful response
    if req is None or req.status_code != 200:
        utils.declare_error("Failed to access registry catalog")

    for image_obj in registry_load_list:
        image = image_obj["image"]
        try:
            result = subprocess.run(
                ["docker", "image", "inspect", image],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            if result.returncode != 0:
                subprocess.run(["docker", "pull", image], check=True)
            
            target_tag = f"k3d-registry.local:12345/{image}"
            subprocess.run(["docker", "tag", image, target_tag], check=True)
            subprocess.run(["docker", "push", target_tag], check=True)
            rprint(f"  -- Loaded: [bright_cyan]{image}")
        
        except subprocess.CalledProcessError as e:
            rprint(f"[yellow]  -- Warning: Failed to process image {image} - {str(e)}")

def start_cluster(progress, task) -> bool:
    """Start cluster with readiness checks"""
    try:
        result = subprocess.run(
            ["k3d", "cluster", "list", "-o", "json"],
            capture_output=True,
            text=True,
            check=True
        )
        clusters = json.loads(result.stdout)
        cluster_exists = any(c["name"] == "k3s-default" for c in clusters)

        if cluster_exists:
            cluster = next(c for c in clusters if c["name"] == "k3s-default")
            if not cluster["serversReady"]:
                subprocess.run(["k3d", "cluster", "start"], check=True)
            return False

        subprocess.run([
            "k3d", "cluster", "create",
            "--registry-use", "k3d-registry.local:12345",
            "-p", "8088:80@loadbalancer",
            "--wait"
        ], check=True)

        utils.wait_for_condition(
            "nodes",
            lambda n: any(
                cond["type"] == "Ready" and cond["status"] == "True"
                for cond in n["status"]["conditions"]
            ),
            timeout=120
        )
        return True

    except subprocess.CalledProcessError as e:
        utils.declare_error(f"Cluster creation failed: {e.stderr}")

def stop_pod(pod: str) -> None:
    """Stop pod with Helm/kubectl detection"""
    try:
        result = subprocess.run(
            ["helm", "status", pod],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        if result.returncode == 0:
            subprocess.run(["helm", "uninstall", pod], check=True)
            rprint(f"    -- {pod} [steel_blue]stopped (via Helm)")
        else:
            subprocess.run(["kubectl", "delete", "pod", pod], check=True)
            rprint(f"    -- {pod} [steel_blue]stopped (via kubectl)")
    
    except subprocess.CalledProcessError as e:
        rprint(f"[yellow]  -- Warning: Failed to stop {pod} - {str(e)}")

def start_pod(pod: Dict) -> None:
    """Start pod with config validation"""
    pod_name = pod["repo"].split("/")[-1].replace(".git", "")
    config_path = os.path.join(CONFIG["code"], pod_name, ".auto/config.yaml")
    
    if not os.path.exists(config_path):
        rprint(f"[yellow]  -- Skipping {pod_name}: Missing config.yaml")
        return

    with open(config_path) as f:
        pod_config = yaml.safe_load(f)
    
    try:
        if pod_config.get("command") == "helm":
            helm_install(pod_name, pod_config)
        else:
            kubectl_apply(pod_name, pod_config)
        
        utils.wait_for_pod_ready(pod_name)
        rprint(f"     * [bright_cyan]{pod_name}[/] installed")

    except Exception as e:
        rprint(f"[red]  -- Failed to start {pod_name}: {str(e)}")

def helm_install(pod_name: str, config: Dict):
    """Handle Helm installations"""
    cmd = [
        "helm", "upgrade", "--install",
        pod_name,
        os.path.join(CONFIG["code"], pod_name, ".auto/helm"),
        *shlex.split(config.get("command-args", ""))
    ]
    subprocess.run(cmd, check=True)

def kubectl_apply(pod_name: str, config: Dict):
    """Handle kubectl installations"""
    k8s_dir = os.path.join(CONFIG["code"], pod_name, ".auto/k8s")
    if not os.path.exists(k8s_dir):
        raise FileNotFoundError(f"Missing k8s directory for {pod_name}")
    
    subprocess.run(["kubectl", "apply", "-f", k8s_dir], check=True)

def restart_pod(pod: str) -> None:
    """Restart pod with status checks"""
    stop_pod(pod)
    utils.wait_for_pod_removal(pod)
    start_pod(pod)

def install_pods_in_cluster() -> None:
    """Install pods with proper resource ordering"""
    try:
        user_path = os.path.expanduser("~")
        subprocess.run(
            ["kubectl", "apply", "-f", f"{user_path}/.auto/k3s/pv.yaml"],
            check=True
        )
        subprocess.run(
            ["kubectl", "apply", "-f", f"{user_path}/.auto/k3s/pvc.yaml"],
            check=True
        )
        
        rprint("  -- Pods:")
        for pod in CONFIG["pods"]:
            start_pod(pod)
            
    except subprocess.CalledProcessError as e:
        utils.declare_error(f"Failed to install pods: {e.stderr}")

def tag_pod_docker_image(pod: str) -> None:
    """Build and tag pod image with validation"""
    try:
        code_path = CONFIG["code"]
        config_path = os.path.join(code_path, pod, ".auto/config.yaml")
        
        with open(config_path) as f:
            pod_config = yaml.safe_load(f)
            version = pod_config["version"]

        subprocess.run(
            ["docker", "build", "-t", f"{pod}:{version}", f"{code_path}/{pod}"],
            check=True
        )
        subprocess.run(
            ["docker", "tag", f"{pod}:{version}", f"k3d-registry.local:12345/{pod}:{version}"],
            check=True
        )
        subprocess.run(
            ["docker", "push", f"k3d-registry.local:12345/{pod}:{version}"],
            check=True
        )
        rprint(f"  -- [bright_cyan]{pod}[/] image built and pushed")

    except (FileNotFoundError, yaml.YAMLError) as e:
        rprint(f"[red]  -- Config error for {pod}: {str(e)}")
    except subprocess.CalledProcessError as e:
        rprint(f"[red]  -- Docker error for {pod}: {str(e)}")

def output_logs(pod):
    """Output the logs for a pod via kubctl"""

    # Is the cluster running or stopped?
    bash_command = """/usr/local/bin/k3d cluster list"""
    if utils.run_and_wait(bash_command, check_result="0/1"):
        rprint("[red]ERROR: Development cluster is not running!")
        return

    pod_name = utils.get_full_pod_name(pod)

    if not pod_name:
        utils.declare_error("Pod not found: {pod}")

    rprint(f"Printing logs for {pod_name}")
    rprint("[steel_blue]Press ^C to exit")

    # Run `kubectl logs` on a pod but exclude the k8s healthchecks.
    # the extra space at the end of the ip address is helpful because the health check
    # ip address and the pod ip address might be similar (i.e. 10.42.1.1 and 10.42.1.10)
    # by adding the space it let's the 10.42.1.10 show up in the output and suppresses
    # the 10.42.1.1 healthcheck
    os.system(
        f'kubectl logs -f {pod_name} | grep -v "10.42.0.1 " | grep -v "10.42.1.1 "'
    )


def seed_pod(pod):
    """Run the seeddb.py script inside a pod's container"""

    # Get the pod config and the init command
    config = utils.get_pod_config(pod)
    seed_command = config["seed-command"]

    # Run the command
    utils.run_command_inside_pod(pod, seed_command)

    # Tell the user
    rprint(f"  -- {pod} database seeded")


def init_pod_db(pod):
    """Run the initdb.py script inside a pod's container"""

    # Get the pod config and the init command
    config = utils.get_pod_config(pod)
    init_command = config["init-command"]

    # Run the command
    utils.run_command_inside_pod(pod, init_command)

    # Tell the user
    rprint(f"  -- {pod} database initialized")


def verify_dependencies():
    """Verify the system has what it needs to run auto"""

    # Check for the docker daemon running and command being available
    utils.check_docker()

    # Check for k3d and kubectl
    utils.check_k8s()

    # Check for helm
    utils.check_helm()

    # Check for hosts entries
    utils.check_registry_host_entry()


def pull_and_build_pods():
    """Pull all git repos, then docker build, then upload the images to the local registry"""

    # Set the code folder from config and notify the user
    code_folder = CONFIG["code"]
    rprint(f" -- using code folder: {code_folder}")

    # Pull each repo so we have it locally
    rprint(" -- pulling code repos")
    for pod in CONFIG["pods"]:
        rprint(f"    = Pulling [bright_cyan]{pod['repo']}[/]")
        utils.pull_repo(pod, code_folder)

    return CONFIG["pods"]


def install_config_from_repo(repo):
    """Install an auto parent config from a repository"""

    # Local vars
    user_path = os.path.expanduser("~")

    # Tell the user
    rprint(f"Installing Parent Config: [bright_cyan]{repo}[/]")

    # If there is already a file there let's back it up
    if os.path.isfile(user_path + "/.auto/config/local.yaml"):
        shutil.move(
            user_path + "/.auto/config/local.yaml",
            user_path + "/.auto/config/local.yaml.bak",
        )

    # Pull the parent repo
    code_repo = {"repo": repo}
    utils.pull_repo(code_repo, CONFIG["code"])

    # Copy the file to the ~/.auto/config/local.yaml folder
    parent_folder = repo.split("/")[-1:][0].replace(".git", "")
    shutil.copy(
        CONFIG["code"] + "/" + parent_folder + "/local.yaml",
        user_path + "/.auto/config/local.yaml",
    )


def migrate_with_smalls(pod):
    """Run the database migrations in a pod with smalls"""

    # Run the command inside the pod
    command = "./smalls.py migrate"
    utils.run_command_inside_pod(pod, command)


def rollback_with_smalls(pod, number):
    """Run the database rollback in a pod with smalls"""

    # Run the command inside the pod
    command = f"./smalls.py rollback {number}"
    utils.run_command_inside_pod(pod, command)
