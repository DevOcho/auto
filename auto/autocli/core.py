"""Imports"""

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml
from autocli import registry, services, utils
from autocli.config import CONFIG
from rich import print as rprint
from rich.console import Console, Group
from rich.live import Live
from rich.progress import Progress
from rich.text import Text


def _setup_https_certificates(pods):
    """Helper to setup HTTPS certificates interactively"""
    rprint("[deep_sky_blue1]Setting up HTTPS certificates...[/]")
    utils.check_mkcert()

    pod_domains = []
    for repo in pods:
        p_name = repo["repo"].split("/")[-1:][0].replace(".git", "")
        pod_domains.append(f"{p_name}.local")

    cert_path = os.path.expanduser("~") + "/.auto/certs"
    key_file, cert_file = utils.create_local_certs(
        cert_path, additional_domains=pod_domains
    )
    rprint(" :white_heavy_check_mark:[green] Certificates Ready")
    return key_file, cert_file


def _print_access_hints(pods, use_https):
    """Helper to print access hints at the end of start"""
    print()
    rprint("[italic]Hint: Some items may still be starting in k3s.")
    rprint("[italic]You can access your pod(s) via the following URLs:")

    protocol = "https" if use_https else "http"
    port_suffix = "" if use_https else ":8088"

    for repo in pods:
        pod_name = repo["repo"].split("/")[-1:][0].replace(".git", "")
        if utils.check_host_entry(pod_name):
            rprint(f"[italic]  {protocol}://{pod_name}.local{port_suffix}/")


def _run_bootstrap_step(msg, success_msg, execute, func=None, **kwargs):
    """Helper to orchestrate standard bootstrap steps cleanly"""
    rprint(f"[deep_sky_blue1]{msg}[/]")
    result = None
    if execute and func:
        result = func()
    rprint(f" :white_heavy_check_mark:[green] {success_msg}")

    if kwargs.get("advance", 0) > 0 and "progress" in kwargs and "task" in kwargs:
        kwargs["progress"].update(kwargs["task"], advance=kwargs["advance"])

    return result


def _install_system_sequence(new_cluster):
    """Helper to run the system pod installation block"""
    services.install_system_pods()
    if new_cluster:
        services.create_databases()


def bootstrap_cluster(pod, dry_run, offline):
    """Orchestrates the entire start sequence seamlessly."""
    pods = CONFIG.get("pods", [])
    use_https = CONFIG.get("https", False)
    key_file, cert_file = "", ""

    if not pod and use_https and not dry_run:
        key_file, cert_file = _setup_https_certificates(pods)

    if pod:
        rprint(f"[steel_blue]Starting[/] {pod}")
        start_pod(pod)
        return

    with Progress(transient=False) as progress:
        task = progress.add_task("Creating Dev Environment", total=100)

        # STEP 1: Verify Dependencies
        _run_bootstrap_step(
            "Verify Dependencies",
            "Dependencies installed and working",
            not dry_run,
            verify_dependencies,
        )

        # STEP 2: Pull Code and Build Local Images
        fetched_pods = _run_bootstrap_step(
            "Pulling code and building local images",
            "Pods built",
            not dry_run and not offline,
            pull_and_build_pods,
        )
        if fetched_pods is not None:
            pods = fetched_pods

        # STEP 3: Container Registry
        _run_bootstrap_step(
            "Container Registry",
            "Registry Ready",
            not dry_run,
            registry.start_registry,
            progress=progress,
            task=task,
            advance=5,
        )

        # STEP 4: Populate Container Registry
        _run_bootstrap_step(
            "Populating Container Registry for faster loading",
            "Registry Populated",
            not dry_run,
            registry.populate_registry,
            progress=progress,
            task=task,
            advance=5,
        )

        # STEP 5: Cluster
        new_cluster = _run_bootstrap_step(
            "Cluster",
            "Cluster Ready",
            not dry_run,
            lambda: start_cluster(
                progress, task, key_file=key_file, cert_file=cert_file
            ),
            progress=progress,
            task=task,
            advance=33,
        )

        # STEP 6: System Pods & Databases
        _run_bootstrap_step(
            "Loading system pods...",
            "System Pods Loaded",
            not dry_run,
            lambda: _install_system_sequence(new_cluster),
            progress=progress,
            task=task,
            advance=20,
        )

        # STEP 7: Build & Load Application Pods
        _run_bootstrap_step(
            "Building and loading pods...",
            "Pods Loaded",
            not dry_run,
            install_pods_in_cluster,
            progress=progress,
            task=task,
            advance=20,
        )

        # STEP 8: Auto-detect external images and cache them
        _run_bootstrap_step(
            "Detecting and caching external images...",
            "External Images Cached",
            not dry_run,
            registry.cache_running_images,
            progress=progress,
            task=task,
            advance=17,
        )

        _print_access_hints(pods, use_https)


def _install_nginx_ingress(use_https, key_file, cert_file):
    """Install and configure Nginx Ingress Controller"""
    rprint("     = Installing Nginx Ingress Controller...")
    utils.run_and_wait(
        "helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx",
        capture_output=True,
    )
    utils.run_and_wait("helm repo update", capture_output=True)

    # Build Helm command
    helm_cmd = (
        "helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx "
        "--namespace ingress-nginx --create-namespace "
        "--set controller.service.type=LoadBalancer "
        "--set controller.watchIngressWithoutClass=true "
        "--set controller.ingressClassResource.default=true "
        "--set controller.admissionWebhooks.enabled=false "
    )

    # New cluster created, if HTTPS, inject secrets and config Nginx
    if use_https and key_file and cert_file:
        rprint("     = Configuring Cluster HTTPS (Nginx)")

        # Create the namespace first (needed for secrets)
        utils.run_and_wait(
            "kubectl create namespace ingress-nginx", capture_output=True
        )

        # Create secrets in default and ingress-nginx namespaces
        for ns in ["default", "ingress-nginx"]:
            cmd = (
                f"kubectl create secret tls local-tls --key {key_file} --cert {cert_file} "
                f"-n {ns} --dry-run=client -o yaml | kubectl apply -f -"
            )
            utils.run_and_wait(cmd, capture_output=True)

        # Add default cert arg
        extra_args = "controller.extraArgs.default-ssl-certificate"
        helm_cmd += f" --set {extra_args}=ingress-nginx/local-tls"

    # Run the Helm install silently
    if not utils.run_and_wait(helm_cmd, capture_output=True):
        rprint("     [red]Error installing Nginx Ingress Controller[/red]")
    else:
        # Explicitly Patch the Deployment to FORCE the argument if Helm missed it
        if use_https:
            patch_cmd = (
                "kubectl patch deployment ingress-nginx-controller -n ingress-nginx "
                '--type=json -p=\'[{"op": "add", "path": '
                '"/spec/template/spec/containers/0/args/-", '
                '"value": "--default-ssl-certificate=ingress-nginx/local-tls"}]\''
            )
            utils.run_and_wait(patch_cmd, capture_output=True, suppress_error=True)

        # Force restart Nginx pods to ensure they pick up the new certificate
        utils.run_and_wait(
            "kubectl rollout restart deployment ingress-nginx-controller -n ingress-nginx",
            capture_output=True,
        )


def _verify_and_heal_connection():
    """Check cluster connection and try to fix it if broken"""
    if not utils.verify_cluster_connection():
        rprint(
            "     [yellow]Warning: Cluster connection failed. Refreshing context...[/yellow]"
        )
        utils.run_and_wait(
            "k3d kubeconfig merge k3s-default --kubeconfig-switch-context"
        )
        if not utils.verify_cluster_connection():
            utils.declare_error(
                """Could not connect to the cluster. Please check your kubeconfig
 or run 'auto stop' then 'auto start'."""
            )


def start_cluster(progress, task, key_file="", cert_file=""):
    """Start a K3D cluster and return if it is new (true) or existing (false)"""

    # HTTPS Setup
    use_https = CONFIG.get("https", False)
    load_bal_config = '--api-port 6550 -p "8088:80@loadbalancer"'

    if use_https:
        load_bal_config = (
            '--api-port 6550 -p "80:80@loadbalancer" -p "443:443@loadbalancer"'
        )

    # 1. CHECK EXISTING CLUSTER
    bash_command = """/usr/local/bin/k3d cluster list"""
    if utils.run_and_wait(bash_command, check_result="k3s-default"):
        rprint("  -- Found existing cluster")

        # Ensure context is current
        utils.run_and_wait(
            "k3d kubeconfig merge k3s-default --kubeconfig-switch-context",
            capture_output=True,
        )

        # Is the cluster stopped? (0/1 servers)
        if utils.run_and_wait(bash_command, check_result="0/1"):
            rprint("     = Cluster is stopped. Starting...")
            bash_command = """k3d cluster start"""
            if not utils.run_and_wait(bash_command):
                utils.declare_error("Failed to start existing cluster.")

        # Verify we can actually talk to it
        _verify_and_heal_connection()

        return False

    # 2. CREATE NEW CLUSTER
    progress.update(task, advance=5)
    print("  -- Creating cluster (this will take a minute)")

    if use_https:
        rprint("  -- [bold green]HTTPS Enabled[/]: Binding ports 80/443")

    code_dir = CONFIG["code"]
    # I'm opening port 8088 outside the cluster for access to the sites
    # Ports for databases are dynamically opened when needed by pods
    bash_command = (
        f"/usr/local/bin/k3d cluster create "
        f"--volume {code_dir}:/mnt/code "
        f"--registry-use k3d-registry.local:12345 "
        f"--registry-config ~/.auto/k3s/registries.yaml "
        f"{load_bal_config} "
        f'--k3s-arg "--disable=traefik@server:0" '
        f"--network k3d-vpn-net"
        f"--agents 1"
    )

    # Attempt creation.
    # Changed capture_output to True to suppress verbose k3d INFO logs.
    # run_and_wait will automatically print the output if the command fails.
    if not utils.run_and_wait(bash_command, capture_output=True):
        utils.declare_error("Failed to create k3d cluster. Check logs above.")
        return False

    # Ensure context is set correctly immediately after creation
    utils.run_and_wait("k3d kubeconfig merge k3s-default --kubeconfig-switch-context")

    # Verify connection immediately
    _verify_and_heal_connection()

    print("     = Cluster Started.  Waiting for Pods to finish starting...")
    progress.update(task, advance=6)

    # Install and Configure Nginx
    _install_nginx_ingress(use_https, key_file, cert_file)

    # Wait for the Ingress Controller to be ready
    if utils.wait_for_pod_status("ingress-nginx-controller", "Running"):
        progress.update(task, advance=5)

    # Let's remove the completed nginx job containers
    if utils.wait_for_pod_status("ingress-nginx-admission-create", "Complete"):
        progress.update(task, advance=5)
    bash_command = """kubectl delete pod -n ingress-nginx \
                      --field-selector=status.phase==Succeeded"""
    if utils.run_and_wait(bash_command):
        print("     = Pods finished starting.  Removed completed setup pods.")

    return True


def stop_cluster(progress, task) -> None:
    """Stop the cluster"""

    print("  -- Stopping cluster")
    bash_command = """/usr/local/bin/k3d cluster stop"""
    utils.run_and_wait(bash_command)
    progress.update(task, advance=50)


def delete_cluster(progress, task) -> None:
    """Delete the cluster"""

    rprint("  -- Deleting cluster :skull::skull:")

    # Explicitly target k3s-default
    delete_cmd = "/usr/local/bin/k3d cluster delete k3s-default"

    # Run delete
    utils.run_and_wait(delete_cmd)

    # Verify deletion by looping with retries
    # We wait up to 45 seconds. If it's still there, we error out.
    for i in range(45):
        try:
            # Check k3d list
            k3d_result = subprocess.run(
                "/usr/local/bin/k3d cluster list",
                shell=True,
                capture_output=True,
                text=True,
                check=False,
            )

            # Check docker containers (source of truth)
            docker_result = subprocess.run(
                "docker ps -a",
                shell=True,
                capture_output=True,
                text=True,
                check=False,
            )

            k3d_gone = (
                k3d_result.returncode == 0 and "k3s-default" not in k3d_result.stdout
            )
            docker_gone = (
                docker_result.returncode == 0
                and "k3d-k3s-default" not in docker_result.stdout
            )

            if k3d_gone and docker_gone:
                progress.update(task, advance=50)
                return

            # If not gone after 5 seconds, try deleting again (idempotent usually)
            if i > 0 and i % 5 == 0:
                utils.run_and_wait(delete_cmd, suppress_error=True)

            time.sleep(1)
        except (OSError, subprocess.SubprocessError):
            time.sleep(1)

    # If we fall out of the loop, deletion failed
    utils.declare_error(
        "Failed to delete cluster k3s-default. Docker containers may be stuck."
    )


def stop_pod(pod) -> None:
    """Stop a single pod"""

    # Local Vars
    code_dir = CONFIG["code"]

    # If we get a dictionary we have to find the pod name from the repo name
    if isinstance(pod, dict):
        pod_name = pod["repo"].split("/")[-1:][0].replace(".git", "")
    else:
        pod_name = pod

    # Is the pod running?
    if not utils.run_and_wait("""kubectl get pods""", check_result=pod_name):
        rprint(f"    -- {pod_name}[steel_blue] was not running")
        return

    # Attempt to load config to perform a clean stop
    config_file_path = Path(code_dir) / pod_name / ".auto" / "config.yaml"

    if not config_file_path.is_file():
        # Fallback for pods without local config
        rprint(
            f"    [yellow]Warning: Config not found for {pod_name}. Trying helm uninstall...[/]"
        )
        utils.run_and_wait(f"helm uninstall {pod_name}")
        return

    with open(config_file_path, encoding="utf-8") as pod_yaml:
        pod_config = yaml.safe_load(pod_yaml)

    # Determine stop strategy based on start command
    start_cmd = pod_config.get("command", "")

    if re.search("helm", start_cmd):
        # Helm Uninstall
        release_name = pod_config.get("name", pod_name)
        command = f"helm uninstall {release_name}"
        utils.run_and_wait(command)
        rprint(f"    -- {pod_name} [steel_blue]stopped (Helm)[/]")

    elif re.search("kubectl apply", start_cmd):
        # Kubectl Delete (reverse of apply)
        args = pod_config.get("command_args", pod_config.get("command-args", ""))

        # If the user put the whole command in `command` (e.g. `kubectl apply -f file.yaml`),
        # we need to extract the `-f file.yaml` part to pass to `kubectl delete`.
        if not args and "-f" in start_cmd:
            args = start_cmd[start_cmd.find("-f") :]  # noqa: E203

        command = f"kubectl delete {args}".strip()

        # Execute in the pod directory so relative paths in args work
        pod_folder = os.path.join(code_dir, pod_name)
        utils.run_and_wait(command, cwd=pod_folder)
        rprint(f"    -- {pod_name} [steel_blue]stopped (Manifest)[/]")

    else:
        # Unknown/Custom command - fallback
        rprint(
            f"    [red]Unknown start command '{start_cmd}'. Attempting helm uninstall...[/]"
        )
        utils.run_and_wait(f"helm uninstall {pod_name}")


def _recover_pvc_conflict(pod_name):
    """Helper to attempt fixing PVC conflicts"""
    rprint("       [italic]Attempting to fix common PVC errors...[/]")

    # Common fix: Delete conflicting 'code' PVC
    # 1. Delete the deployment to release the volume claim lock
    utils.run_and_wait(f"kubectl delete deployment {pod_name} --ignore-not-found=true")

    # 2. Delete the conflicting PVC
    utils.run_and_wait("kubectl delete pvc code --ignore-not-found=true")

    # 3. Wait for PVC to be fully removed
    for _ in range(15):
        if not utils.run_and_wait(
            "kubectl get pvc code", capture_output=True, suppress_error=True
        ):
            break
        time.sleep(1)

    # 4. Restore the global PV and PVC so they are correct for this and other pods
    user_path = os.path.expanduser("~")
    utils.run_and_wait(
        f"kubectl apply -f {user_path}/.auto/k3s/pv.yaml", suppress_error=True
    )
    utils.run_and_wait(
        f"kubectl apply -f {user_path}/.auto/k3s/pvc.yaml", suppress_error=True
    )


def _build_install_command(pod_config, pod_name, code_dir):
    """Helper to construct the installation command"""
    release_name = pod_config.get("name", pod_name)
    is_helm = False

    base_cmd = pod_config.get("command", "")
    # Fallback support for both spellings of command args, preventing KeyError
    cmd_args = pod_config.get("command_args", pod_config.get("command-args", ""))

    # If they are using helm
    if re.search("helm", base_cmd):
        is_helm = True
        desc = pod_config.get("desc", "")
        helm_path = f"{code_dir}/{pod_name}/.auto/helm"

        # Construct helm command
        command = f'{base_cmd} {cmd_args} --description "{desc}" {release_name} {helm_path}'.strip()
    else:
        # They are using kubectl apply
        command = f"{base_cmd} {cmd_args}".strip()

    return command, is_helm, release_name


def _execute_pod_install(command, pod_folder, pod_name, is_helm, release_name):
    """Helper to execute the installation command with retries"""
    # FIRST ATTEMPT: Run silently to avoid scary error messages for known issues
    if utils.run_and_wait(command, cwd=pod_folder, suppress_error=True):
        rprint(f"     * [bright_cyan]: {pod_name}[/] installed")
    else:
        # If failed, attempt auto-fix silently
        _recover_pvc_conflict(pod_name)

        # If it was Helm, try to uninstall the partial/failed release before retrying
        if is_helm:
            utils.run_and_wait(
                f"helm uninstall {release_name}",
                capture_output=True,
                suppress_error=True,
            )

        # RETRY INSTALLATION
        if utils.run_and_wait(command, cwd=pod_folder):
            rprint(f"     * [bright_cyan]: {pod_name}[/] installed")
        else:
            rprint(f"     * [red]: {pod_name}[/] failed to install")


def start_pod(pod) -> None:
    """Start a single pod"""

    # Local Vars
    code_dir = CONFIG["code"]

    # If we get a dictionary we have to find the pod name from the repo name
    if isinstance(pod, dict):
        pod_name = pod["repo"].split("/")[-1:][0].replace(".git", "")
    else:
        pod_name = pod

    # Is this pod already running?
    if utils.run_and_wait("""kubectl get pods""", check_result=pod_name):
        rprint(f"       * {pod_name}: [steel_blue]already running")
        return

    # If we aren't running let's start via helm install or kubectl apply
    config_file_path = Path(code_dir) / pod_name / ".auto" / "config.yaml"

    if not config_file_path.is_file():
        utils.declare_error(
            f"[bold red]Error: Configuration file not found at: {config_file_path}[/bold red]",
            exit_auto=True,
        )
        return

    with open(config_file_path, encoding="utf-8") as pod_yaml:
        pod_config = yaml.safe_load(pod_yaml)

    # Prepare execution directory (repo folder)
    pod_folder = os.path.join(code_dir, pod_name)

    command, is_helm, release_name = _build_install_command(
        pod_config, pod_name, code_dir
    )

    # Run the pod install command inside the repo directory
    _execute_pod_install(command, pod_folder, pod_name, is_helm, release_name)


def restart_pod(pod) -> None:
    """Stop then start a pod"""

    # How many times are we going to try this?
    max_retries = 15

    stop_pod(pod)
    while utils.verify_pod_is_installed(pod) and max_retries >= 1:
        rprint(f"       * [steel_blue]Portal [/]{pod} [steel_blue]still running")
        time.sleep(2)
        max_retries -= 1
    start_pod(pod)


def install_pods_in_cluster() -> None:
    """Install Pods into the cluster"""

    # Let's setup the code directory PV and PVC in k3s
    user_path = os.path.expanduser("~")
    command = f"kubectl apply -f {user_path}/.auto/k3s/pv.yaml"
    utils.run_and_wait(command)
    command = f"kubectl apply -f {user_path}/.auto/k3s/pvc.yaml"
    utils.run_and_wait(command)

    # Now let's start all the pods
    rprint("  -- Pods:")
    for pod in CONFIG["pods"]:
        start_pod(pod)


def output_logs(pod):
    """Output the logs for a pod via kubctl"""

    # Is the cluster running or stopped?
    bash_command = """/usr/local/bin/k3d cluster list"""
    if utils.run_and_wait(bash_command, check_result="0/1"):
        rprint("[red]ERROR: Development cluster is not running!")
        return

    pod_name = utils.get_full_pod_name(pod)

    if not pod_name:
        utils.declare_error(f"Pod not found: {pod}")

    # Dynamically find the Node IP (often the source of the health check)
    node_ip = utils.run_and_return(
        "kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type==\"InternalIP\")].address}'"
    )

    rprint(f"Printing logs for {pod_name}")
    rprint("[italic]Filtering out health checks (kube-probe, node-ip, 10.42.x.1)...[/]")
    rprint("[steel_blue]Press ^C to exit")

    # Build the filter command
    # 1. --line-buffered removes any lag issue (grep usually buffers heavily on pipes)
    # 2. -v "kube-probe" filters standard HTTP health checks regardless of IP
    # 3. -v node_ip filters TCP checks coming from the kubelet
    filters = [
        'grep --line-buffered -v "kube-probe"',
        'grep --line-buffered -v "10.42.0.1 "',
        'grep --line-buffered -v "10.42.1.1 "',
    ]

    if node_ip:
        filters.append(f'grep --line-buffered -v "{node_ip}"')

    filter_cmd = " | ".join(filters)

    # Run kubectl logs piped through our filters
    # os.system gives a direct stream without a python buffer
    os.system(f"kubectl logs -f {pod_name} | {filter_cmd}")


def verify_dependencies():
    """Verify the system has what it needs to run auto"""

    # If there are errors let's get a count of them
    errors = 0

    # Check for the docker daemon running and command being available
    errors += utils.check_docker()

    # Check for k3d and kubectl
    errors += utils.check_k8s()

    # Check for helm
    errors += utils.check_helm()

    # Check for hosts entries
    errors += utils.check_registry_host_entry()

    if errors:
        rprint(f"[red]There were {errors} so we stopped the command[/red]")
        sys.exit(1)

    # If HTTPS is enabled, check for mkcert and certutil
    if CONFIG.get("https", False):
        utils.check_mkcert()


def show_status(namespace="default", all_namespaces=False, watch=False):
    """Show the status of the cluster and pods"""

    console = Console()

    # Clear the terminal if watching so it starts at the top
    if watch:
        console.clear()

    def generate_content():
        """Generate the renderable content (Group) for the status"""
        items = []

        # Header
        items.append(Text("Auto Status", style="deep_sky_blue1 bold"))
        items.append(Text(""))  # Spacer

        # 1. Check K3d Cluster
        c_stat, c_style = utils.get_cluster_status()
        items.append(Text.assemble(" Cluster:  ", (c_stat, c_style)))

        # 2. Check Registry
        r_stat, r_style = utils.get_registry_status()
        items.append(Text.assemble(" Registry: ", (r_stat, r_style)))

        # If the cluster is stopped, we can't show pods
        if c_stat != "Running":
            items.append(
                Text(
                    "\nCluster is stopped. Run 'auto start' to start it.",
                    style="italic",
                )
            )
            return Group(*items)

        # 3. Pods Table
        items.append(Text(""))  # Spacer
        table_title = (
            "Pods (All Namespaces)"
            if all_namespaces
            else f"Pods (Namespace: {namespace})"
        )
        items.append(Text(table_title, style="deep_sky_blue1"))

        # Build the table using helper
        items.append(utils.build_pod_table(namespace, all_namespaces))

        return Group(*items)

    # Main Execution Logic
    if watch:
        # Use Live to update in-place without strobe
        with Live(generate_content(), console=console, refresh_per_second=4) as live:
            while True:
                try:
                    time.sleep(3)
                    live.update(generate_content())
                except KeyboardInterrupt:
                    break
    else:
        # Just print once
        rprint(generate_content())


def pull_and_build_pods():
    """Pull all git repos, then docker build, then upload the images to the local registry"""

    # Set the code folder from config and notify the user
    code_folder = CONFIG["code"]
    rprint(f" -- using code folder: {code_folder}")

    # Pull each repo so we have it locally
    rprint(" -- pulling code repos")
    for pod in CONFIG["pods"]:
        rprint(f"    = Pulling [bright_cyan]{pod['repo']}[/]")
        utils.ensure_host_known(pod["repo"])
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
