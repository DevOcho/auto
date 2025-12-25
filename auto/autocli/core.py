"""Imports"""

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests
import yaml
from autocli import utils
from rich import print as rprint
from rich.console import Console, Group
from rich.live import Live
from rich.text import Text

# Read Config and provide globally
CONFIG = utils.load_config()


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


def populate_registry():
    """Load important images into the registry to speed up the deployment of pods"""

    # Local vars
    skip_version = False

    # Get the list of images we want to pre-load from the config file
    registry_load_list = CONFIG["registry"]

    # Get the registry images already loaded
    req = requests.get("http://k3d-registry.local:12345/v2/_catalog", timeout=30)
    registry_list = req.json()

    # Remove images that are already loaded in the registry since we don't need to load them
    for image in registry_list["repositories"]:
        for delete_candidate in registry_load_list:
            if re.search(delete_candidate["image"].split(":")[0], image):
                registry_load_list.remove(delete_candidate)

    # Load new images in the registry
    for image_obj in registry_load_list:
        image = image_obj["image"]

        # Is this a local image already?
        bash_command = "docker images"
        if not utils.run_and_wait(bash_command, check_result=image):
            bash_command = "docker pull " + image
            utils.run_and_wait(bash_command)

        # Let's tag the image for the registry
        bash_command = "docker tag " + image + " k3d-registry.local:12345/" + image
        utils.run_and_wait(bash_command)

        # Push the image into the registry
        bash_command = "docker push k3d-registry.local:12345/" + image
        utils.run_and_wait(bash_command)
        rprint("  -- Loaded: [bright_cyan]" + image)

    # Now we need to build and load the pods
    for pod in CONFIG["pods"]:
        # Check each pod
        skip_version = False

        # What is the name of this pod?
        pod_name = pod["repo"].split("/")[-1:][0].replace(".git", "")

        # Since this is a slow process, let's skip the ones that are already loaded
        if pod_name in registry_list["repositories"]:
            req = requests.get(
                f"http://k3d-registry.local:12345/v2/{pod_name}/tags/list", timeout=30
            )
            image_info = req.json()

            # We need to load the pod's config and see what version we are on
            with open(
                CONFIG["code"] + "/" + pod_name + "/.auto/config.yaml", encoding="utf-8"
            ) as pod_config_yaml:
                pod_config = yaml.safe_load(pod_config_yaml)
            version = pod_config["version"]

            # search for this version so we know if we need to skip it
            for tag in image_info["tags"]:
                if tag == version:
                    skip_version = True

            # If the loaded image is the same version as one already there we are skipping it
            if skip_version or image_info["tags"] == version:
                continue

        # Build, Tag, and Load the pod image into the registry
        tag_pod_docker_image(pod_name)


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
    # I'm opening port 8088 and 3306 outside the cluster for access to the sites
    # and mysql.  This means you can't run a mysql instance on your host on the
    # same port.
    bash_command = f"""/usr/local/bin/k3d cluster create \
                       --volume {code_dir}:/mnt/code \
                       --registry-use k3d-registry.local:12345 \
                       --registry-config ~/.auto/k3s/registries.yaml \
                       {load_bal_config} \
                       --k3s-arg "--disable=traefik@server:0" \
                       -p "3306:30036@loadbalancer" \
                       --agents 1"""

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
    bash_command = """/usr/local/bin/k3d cluster delete k3s-default"""

    # Run delete
    utils.run_and_wait(bash_command)
    time.sleep(2)  # Give docker a moment to cleanup

    # Verify deletion by looping
    # If k3d cluster list still returns 'k3s-default', we wait.
    # We use capture_output=True so we can check the text result cleanly.
    for _ in range(30):
        try:
            # We run subprocess directly here to differentiate between "command failed"
            # and "text not found".
            result = subprocess.run(
                "/usr/local/bin/k3d cluster list",
                shell=True,
                capture_output=True,
                text=True,
                check=False,
            )

            # If the command succeeded (k3d is running) but 'k3s-default' is NOT in output
            if result.returncode == 0 and "k3s-default" not in result.stdout:
                break

            # If the command failed (e.g. docker daemon busy), we wait and retry
            time.sleep(1)
        except (OSError, subprocess.SubprocessError):
            time.sleep(1)

    progress.update(task, advance=50)


def stop_pod(pod) -> None:
    """Stop a single pod"""

    # Is the pod running?
    if not utils.run_and_wait("""kubectl get pods""", check_result=pod):
        rprint(f"    -- {pod}[steel_blue] was not running")
    else:
        command = f"""helm delete {pod}"""
        utils.run_and_wait(command)
        rprint(f"    -- {pod} [steel_blue]stopped")


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


def _build_install_command(pod_config, pod_name, code_dir):
    """Helper to construct the installation command"""
    release_name = pod_config.get("name", pod_name)
    is_helm = False

    # If they are using helm
    if re.search("helm", pod_config["command"]):
        is_helm = True
        cmd_args = pod_config.get("command-args", "")
        desc = pod_config.get("desc", "")
        helm_path = f"{code_dir}/{pod_name}/.auto/helm"

        # Construct helm command
        command = f'{pod_config["command"]} {cmd_args} --description "{desc}" {release_name} {helm_path}'
    else:
        # They are using kubectl apply
        command = f"{pod_config['command']} {pod_config['command_args']}"

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


def _run_command_with_retry(command):
    """Helper to run a command with retries"""
    for _ in range(10):
        try:
            # Attempt to apply with suppressed errors for cleaner startup logs
            success = utils.run_and_wait(
                command, capture_output=True, suppress_error=True
            )
            if success:
                break
            time.sleep(2)
        except Exception:  # pylint: disable=broad-except
            pass
    else:
        # If we exhausted retries, try one last time WITH errors to show user
        if not utils.run_and_wait(command):
            rprint(f"    [red]Error running {command}")


def install_system_pods():
    """Install all of the system pods in the cluster"""

    # Let's start the ones that we find that are "active"
    for pod in CONFIG["system-pods"]:
        if pod["pod"]["active"]:
            rprint("  -- Starting: " + pod["pod"]["name"])
            for command in pod["pod"]["commands"]:
                _run_command_with_retry(command)

            # MinIO has some extra setup stuff needed to use it
            if pod["pod"]["name"] == "minio":
                utils.setup_minio()


def install_mysql_in_cluster() -> None:
    """Install MySQL into the cluster"""

    print("  -- Installing MySQL into the cluster")

    user_path = os.path.expanduser("~")
    command = f"kubectl apply -f {user_path}/.auto/k3s/mysql/pv.yaml"
    utils.run_and_wait(command)
    command = f"kubectl apply -f {user_path}/.auto/k3s/mysql/pvc.yaml"
    utils.run_and_wait(command)
    command = f"kubectl apply -f {user_path}/.auto/k3s/mysql/deployment.yaml"
    utils.run_and_wait(command)
    command = f"kubectl apply -f {user_path}/.auto/k3s/mysql/service.yaml"
    utils.run_and_wait(command)

    # Let's wait for it to start before we let other pods start
    if utils.wait_for_pod_status("mysql", "Running"):
        rprint("       [green]Started MySQL")


def _process_pod_databases(pod_config):
    """Helper to process database creation for a single pod config"""
    if "system-pods" in pod_config:
        for system_pod in pod_config["system-pods"]:
            if system_pod["name"] == "mysql":
                for database in system_pod["databases"]:
                    utils.create_mysql_database(database["name"])
                    rprint(
                        f"      *  Created MySQL database: [bright_cyan]{database['name']}"
                    )
            elif system_pod["name"] == "minio":
                for bucket in system_pod["buckets"]:
                    utils.create_minio_bucket(bucket["name"])
                    rprint(
                        f"      *  Created MinIO bucket: [bright_cyan]{bucket['name']}"
                    )


def create_databases():
    """Create the databases"""
    rprint("  -- Creating Databases and Buckets")

    # Let's confirm mysql is running
    for system_pod in CONFIG["system-pods"]:
        if system_pod["pod"]["name"] == "mysql":
            # Let's wait for the MySQL pod to start
            if utils.wait_for_pod_status("mysql", "Running"):
                rprint("       [green]MySQL running")

                # Check for actual connectivity via socket before proceeding
                if not utils.wait_for_mysql_socket():
                    rprint(
                        "       [red]MySQL failed to respond on socket after waiting."
                    )
                    return

    # Create the databases requested in each of the pods
    for pod in CONFIG["pods"]:
        pod_name = pod["repo"].split("/")[-1:][0].replace(".git", "")
        pod_config = utils.get_pod_config(pod_name)
        _process_pod_databases(pod_config)


def connect_to_mysql() -> None:
    """Connect to the MySQL cluster inside the k3s cluster"""

    utils.connect_to_db()


def connect_to_postgres() -> None:
    """Connect to the PostgreSQL cluster inside the k3s cluster"""

    utils.connect_to_db_postgres()


def connect_to_minio() -> None:
    """Open a port=forward and print a nice message to inform user"""

    # Helpful Message
    rprint("Open a browser and visit: http://127.0.0.1:9090/")
    rprint("Press ctrl+c to exit")
    rprint("")
    rprint("Username: minio")
    rprint("Password: minio123")
    rprint("")

    # The port forward
    utils.connect_to_minio()


def tag_pod_docker_image(pod) -> None:
    """Tag and push a new docker image to local registry"""

    # Local Vars
    code_path = CONFIG["code"]

    # We need to load the pod's config and see what version we are on
    with open(
        CONFIG["code"] + "/" + pod + "/.auto/config.yaml", encoding="utf-8"
    ) as pod_config_yaml:
        pod_config = yaml.safe_load(pod_config_yaml)
    version = pod_config["version"]

    rprint(f"  -- Building and Tagging: [bright_cyan]{pod} {version}")

    # Verify the pod is real using the users source code folder
    if os.path.isdir(code_path + "/" + pod):
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
    local_pod_names = []
    for p in CONFIG.get("pods", []):
        if "repo" in p:
            name = p["repo"].split("/")[-1:][0].replace(".git", "")
            local_pod_names.append(name)

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
