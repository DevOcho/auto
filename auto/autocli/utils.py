"""Utils for the auto commands"""

import configparser
import os
import re
import shlex
import socket
import subprocess
import sys
from subprocess import CalledProcessError
from time import sleep

import yaml
from autocli.config import CONFIG
from rich import print as rprint
from rich.table import Table
from rich.text import Text


def ensure_host_known(git_url):
    """Ensure the git host is in known_hosts to prevent interactive prompts hanging"""
    # Extract domain from git@github.com:User/Repo.git
    # If https is used, we don't need SSH keys
    domain_match = re.search(r"@(.*?):", git_url)
    if not domain_match:
        return

    host = domain_match.group(1)

    # 1. Check if host is already known
    cmd_check = f"ssh-keygen -F {host}"
    if run_and_wait(cmd_check, capture_output=True, suppress_error=True):
        return  # Host is known

    # 2. If not known, scan and add keys
    rprint(f"  [yellow]-- Trusting new host: {host}[/]")
    ssh_dir = os.path.expanduser("~/.ssh")
    if not os.path.exists(ssh_dir):
        os.makedirs(ssh_dir, mode=0o700)

    try:
        result = subprocess.run(
            ["ssh-keyscan", "-H", host],
            capture_output=True,
            check=True,
            text=True,
        )

        keys = result.stdout.strip()
        if not keys:
            rprint(f"     [red]Warning: Could not retrieve keys for {host}[/]")
            return

        # Append to known_hosts using Python
        known_hosts_path = os.path.join(ssh_dir, "known_hosts")
        with open(known_hosts_path, "a", encoding="utf-8") as f:
            f.write("\n" + keys + "\n")

        rprint(f"     [green]Host {host} added to known_hosts[/]")

    except (CalledProcessError, FileNotFoundError, OSError) as e:
        # FileNotFoundError usually means ssh-keyscan is missing
        rprint(f"     [red]Failed to automatically trust {host}: {e}[/]")
        rprint(
            "     [italic]You may need to run 'git clone' manually once to accept the host key.[/]"
        )


def run_command_inside_pod(pod, command):
    """Run a command inside a pod"""

    # Verify this pod is installed and running
    pod_name = get_full_pod_name(pod)
    if not pod_name:
        declare_error(f"[bright_cyan]{pod}[/bright_cyan] pod is not running")

    # Get the pod config and the init command
    config = get_pod_config(pod)

    # Init the database
    if config:
        command = f"kubectl exec -ti {pod_name} -- /mnt/code/{pod}/{command}"
        run_and_wait(command, capture_output=False)

    else:
        declare_error(f"  !! {pod} could [red]NOT[/red] run command")


def declare_error(error_msg: str, exit_auto: bool = True) -> None:
    """Print an error message and exit"""

    rprint(f"\n [red]:x: Error[/red]: {error_msg}")

    # If they want us to exit then let's stop everything
    if exit_auto:
        sys.exit()


def run_and_wait(
    cmd: str,
    capture_output=True,
    check_result="",
    cwd=None,
    suppress_error=False,
    _retry_count=0,
) -> int:
    """Run a Bash command and wait for it to finish"""

    # Local vars
    found = 0

    # Run the command and return the output
    try:
        output = subprocess.run(
            cmd,
            capture_output=capture_output,
            shell=True,
            check=True,
            cwd=cwd,  # Allow running in specific directory
        )

        if check_result:
            results = output.stdout.splitlines()
            for line in results:
                if re.search(check_result, str(line)):
                    found = 1

            # Returning either that the check was successful (if there was a check)
            # or that the command was successful (if there wasn't a check)
            return found

        # Get to this point implies success
        return 1

    except CalledProcessError as error:
        # Check for kubectl connection issues to auto-heal
        err_text = error.stderr.decode("utf-8") if error.stderr else ""
        if "kubectl" in cmd and (
            "connection refused" in err_text or "server was refused" in err_text
        ):
            if _retry_count < 3:
                # Attempt to fix connectivity by refreshing kubeconfig
                # We use subprocess directly to avoid recursion loops
                subprocess.run(
                    "k3d kubeconfig merge k3s-default --kubeconfig-switch-context",
                    shell=True,
                    capture_output=True,
                    check=False,
                )
                sleep(2)
                # Retry the original command
                return run_and_wait(
                    cmd,
                    capture_output,
                    check_result,
                    cwd,
                    suppress_error,
                    _retry_count + 1,
                )

        # If we captured output and errors are not suppressed, print the error.
        if capture_output and err_text and not suppress_error:
            rprint(f"\n[red]Command failed:[/red] {cmd}")
            # Use standard print to avoid rich parsing error contents as tags
            print(err_text)
        return 0


def run_and_return(cmd: str) -> str:
    """Run a Bash command and return the output as a string"""

    # Run the command and return the output
    try:
        output = subprocess.run(cmd, capture_output=True, shell=True, check=True)
        return output.stdout.decode("utf-8").strip()
    except CalledProcessError:
        return ""


def run_silent(cmd, merge_stderr=False):
    """Run a shell command discarding its output, raising on failure.

    The shared "fire it and don't care about output, but blow up if it fails"
    pattern used by the database/object-store helpers (which retry on the
    resulting CalledProcessError). ``merge_stderr=True`` routes stderr into the
    (discarded) stdout, matching callers that previously set stderr=STDOUT.
    """
    subprocess.run(
        cmd,
        shell=True,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT if merge_stderr else subprocess.DEVNULL,
    )


def run_async(cmd: str) -> bytes:
    """Run a Bash command and keep moving"""

    # Make this command safe to run
    cmd = shlex.quote(cmd)
    # args = shlex.split(cmd)

    # Run the command
    with subprocess.Popen(cmd, shell=True) as subp:
        output = subp.communicate()[0]
        if subp.returncode < 0:
            cmd = " ".join(cmd)
            sys.stderr.write(f"{cmd} failed")
        return output


def verify_pod_is_installed(pod: str) -> bool:
    """Verify there is still a pod in the cluster"""

    # Get the full name of the pod
    pod_name = get_full_pod_name(pod)

    # If we found a pod by name or we see it in the kubectl get pods command
    # the pod is still "installed" in k3s
    return pod_name or run_and_wait("""kubectl get pods""", check_result=pod)


def verify_cluster_connection(retries=10) -> bool:
    """Verify that kubectl can connect to the cluster"""
    cmd = "kubectl cluster-info"
    for _ in range(retries):
        try:
            # We assume capture_output=True inside run_and_wait is fine here,
            # but we use subprocess directly to avoid loop recursion logging
            subprocess.run(cmd, capture_output=True, shell=True, check=True)
            return True
        except CalledProcessError:
            sleep(2)
    return False


def wait_for_pod_status(podname: str, status: str, max_wait_time=60) -> bool:
    """Check for a pod to be complete and then return"""

    # Local vars
    pod_complete = 0
    cycles = 0  # Each cycle is a half a second

    while not pod_complete and cycles < max_wait_time:
        # Get the pod(s) in question.
        # We DO NOT use grep here so we can detect if kubectl itself fails.
        bash_command = "kubectl get pods --all-namespaces"

        try:
            results = subprocess.run(
                bash_command, capture_output=True, shell=True, check=True
            )

            # Look for the pod and the status to see if it's ready
            result_lines = results.stdout.splitlines()
            for line in result_lines:
                line_str = line.decode("utf-8")
                if re.search(podname, line_str):
                    if re.search(status, line_str):
                        pod_complete = 1
        except CalledProcessError:
            pass

        cycles += 1
        sleep(0.5)

    return bool(pod_complete)


def get_full_pod_name(pod, only_running=True) -> str:
    """Get the full name of the pod for a k3s pod by application name"""

    if only_running:
        cmd = (
            f"kubectl get pods | grep {pod} "
            + "| grep Running | awk 'NR==1{{print $1}}'"
        )
    else:
        cmd = f"kubectl get pods | grep {pod} " + "| awk 'NR==1{{print $1}}'"

    # Make this command safe to run
    cmd = shlex.quote(cmd)
    args = shlex.split(cmd)

    # Run the command and return the output
    pod_name = subprocess.run(args, capture_output=True, shell=True, check=True)

    # give the people what they want
    return pod_name.stdout.decode().strip("\n")


def pull_repo(repo, code_folder):
    """Pull a code repository to the code folder"""

    # Determine where to put this repo based on the code_folder + git project name
    repo_local_dir = (
        code_folder + "/" + repo["repo"].split("/")[-1:][0].replace(".git", "")
    )

    # We need to capture the cwd so we can come back here
    cwd = os.getcwd()

    # Does this repo exist on this system?
    if os.path.exists(repo_local_dir):
        # change to the repo folder so we can run `git status`
        os.chdir(repo_local_dir)
        cmd = "git status"
        if not run_and_wait(cmd, check_result="nothing to commit, working tree clean"):
            # If that didn't work tell the user and then reset and leave
            rprint(
                f"[yellow]       :warning: Not pulling {repo['repo']} because there are untracked changes"
            )
            os.chdir(cwd)
            return

        # Fetch then fast-forward only — never discards local commits.
        # On divergence (incl. remote force-push), --ff-only fails cleanly
        # with zero working-tree mutation instead of resetting.
        run_and_wait(f"git fetch {repo['repo']}", capture_output=True)
        cmd = "git merge --ff-only FETCH_HEAD"
        if not run_and_wait(cmd):
            rprint(
                f"[yellow]       :warning: {repo['repo']} is out of sync with the remote "
                "(local commits not on remote) — left untouched"
            )
            os.chdir(cwd)
            return

    else:
        try:
            # Repo isn't already present so we will need to clone it
            os.chdir(code_folder)
            cmd = f"git clone {repo['repo']}"
            if not run_and_wait(cmd):
                rprint(
                    f"[yellow]       :warning: Could not clone {repo['repo']} for unknown reasons"
                )
            else:
                os.chdir(repo_local_dir)
                cmd = f"git checkout {repo['branch']}"
                if not run_and_wait(cmd):
                    rprint(
                        f"[yellow]       :warning: Could not change to branch {repo['branch']}"
                    )
                os.chdir(cwd)

        except CalledProcessError:
            rprint(f"[yellow]       :warning: Could not clone {repo['repo']}")
            rprint(
                "[yellow]       :warning: Make sure the repository exists and you have permission to clone it"
            )

    # Now change back to the previous cwd so everything is copacetic
    os.chdir(cwd)


def get_pod_config(pod):
    """Get the individual config for a pod"""

    # Local Vars
    config = {}

    # Read globally imported config
    config_file = CONFIG["code"] + "/" + pod + "/.auto/config.yaml"

    # Does the config file exist?
    if not os.path.isfile(config_file):
        declare_error(f"Config file not found at: {config_file}")

    # Load the config file for this pod
    configparser.ConfigParser()
    with open(config_file, encoding="utf-8") as config_handle:
        config = yaml.safe_load(config_handle)

    return config


def get_required_system_pods(config):
    """Determine which system pods need to be started based on global and pod configs"""
    required_pods = set()

    # 1. Globally active system pods from ~/.auto/config/local.yaml
    if "system-pods" in config:
        for sys_pod in config["system-pods"]:
            if sys_pod.get("pod", {}).get("active"):
                required_pods.add(sys_pod["pod"]["name"])

    # 2. Extract implied system pod requirements by crawling inside pulled application repositories
    code_dir = config.get("code", "")
    for pod in config.get("pods", []):
        if isinstance(pod, dict):
            pod_name = pod.get("repo", "").split("/")[-1:][0].replace(".git", "")
        else:
            pod_name = pod

        if not pod_name:
            continue

        config_file_path = os.path.join(code_dir, pod_name, ".auto", "config.yaml")
        if os.path.isfile(config_file_path):
            try:
                with open(config_file_path, encoding="utf-8") as pod_yaml:
                    pod_config = yaml.safe_load(pod_yaml)
                    if pod_config and "system-pods" in pod_config:
                        for req_sys_pod in pod_config["system-pods"]:
                            required_pods.add(req_sys_pod["name"])
            except (OSError, yaml.YAMLError):
                # Pass gracefully if we hit a permission/read issue or badly formatted yaml
                pass

    return required_pods


def is_port_in_use(port: int) -> bool:
    """Check if a port is in use on localhost"""
    in_use = False

    # 1. Look for legacy IPv4 blocks
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        # returns 0 if connection succeeds, meaning port is mapped
        in_use = s.connect_ex(("127.0.0.1", port)) == 0

    # 2. Scan IPv6 scope just in case software like mariadb bounds differently to it.
    if not in_use:
        try:
            with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as s:
                in_use = s.connect_ex(("::1", port)) == 0
        except OSError:
            pass  # Operating System doesn't support IPV6 routing

    return in_use


def is_port_exposed_on_k3d(port: int) -> bool:
    """Check if a port is currently exposed dynamically on the k3d serverlb container"""
    output = run_and_return("docker port k3d-k3s-default-serverlb")
    if not output:
        return False
    # Target string will map back similarly to: '30036/tcp -> 0.0.0.0:3306'
    for line in output.splitlines():
        if f":{port}" in line:
            return True
    return False


def get_cluster_status():
    """Helper to check K3d cluster status"""
    status = "Stopped"
    style = "red"

    # Check if k3d is even installed and lists the cluster
    # suppress_error=True prevents Docker daemon errors when cluster is simply stopped
    if run_and_wait("k3d cluster list", check_result="NAME", suppress_error=True):
        # Check if running (1/1 servers running)
        if run_and_wait("k3d cluster list", check_result="1/1", suppress_error=True):
            status = "Running"
            style = "green"
    return status, style


def get_registry_status():
    """Helper to check Docker registry status"""
    status = "Stopped"
    style = "red"
    if run_and_wait("docker ps", check_result="k3d-registry.local"):
        status = "Running"
        style = "green"
    return status, style


def build_pod_table(namespace, all_namespaces):
    """Helper to build the pods table"""
    table = Table(show_header=True, header_style="bold magenta", expand=True)

    if all_namespaces:
        table.add_column("Namespace", style="dim")

    table.add_column("Pod Name")
    table.add_column("Ready")
    table.add_column("Status")
    table.add_column("Restarts", justify="right")
    table.add_column("Age", justify="right")

    # Build the command based on arguments
    if all_namespaces:
        cmd = "kubectl get pods --all-namespaces --no-headers"
    else:
        cmd = f"kubectl get pods -n {namespace} --no-headers"

    output = run_and_return(cmd)

    if not output:
        return Text(" No pods found.", style="italic")

    for line in output.splitlines():
        parts = line.split()

        # Handle parsing differences between -A and -n
        if all_namespaces:
            # Columns: NAMESPACE NAME READY STATUS RESTARTS AGE
            if len(parts) < 6:
                continue
            ns, name, ready, status, restarts, age = (
                parts[0],
                parts[1],
                parts[2],
                parts[3],
                parts[4],
                parts[5],
            )
        else:
            # Columns: NAME READY STATUS RESTARTS AGE
            if len(parts) < 5:
                continue
            ns = namespace
            name, ready, status, restarts, age = (
                parts[0],
                parts[1],
                parts[2],
                parts[3],
                parts[4],
            )

        # Clean up Age column (remove leading parenthesis)
        age = age.lstrip("(")

        # Colorize Status
        status_style = "green"
        if status not in ["Running", "Completed"]:
            status_style = "yellow"
        if "Error" in status or "Crash" in status or "ImagePullBackOff" in status:
            status_style = "red"

        # Add row to table
        row_data = []
        if all_namespaces:
            row_data.append(ns)

        row_data.extend(
            [
                name,
                ready,
                f"[{status_style}]{status}[/{status_style}]",
                restarts,
                age,
            ]
        )

        table.add_row(*row_data)

    return table


def get_pod_status(pod):
    """Extracts the Status column of a specific pod, in any phase
    (Pending, Running, Succeeded, Failed, Unknown) or any other error
    states

    Args:
        pod (str): The FULL name of the pod to check
    Returns:
        status (str): Pod status (Running | Error | CrashLoopBackOff | etc)
    Raises:
        None: Returns None if the pod is not found or if there is an error running the command
    """
    if not pod:
        return None

    cmd = f"kubectl get pod {pod} --no-headers | awk '{{print $3}}'"
    # Run the command and return the raw STATUS column kubectl shows
    status = run_and_return(cmd)

    if not status:
        return None
    # Take the first line of output and strip whitespace to get the status
    return status.splitlines()[0].strip()
