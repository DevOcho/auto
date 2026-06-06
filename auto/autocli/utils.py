"""Utils for the auto commands"""

# pylint: disable=too-many-lines

import configparser
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import uuid
from subprocess import CalledProcessError
from time import sleep

import yaml
from autocli import platform
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

    # OpenSSH is an optional component on Windows; if the tools aren't present we
    # skip host-key trusting entirely (HTTPS remotes are unaffected).
    if not platform.which("ssh-keyscan") or not platform.which("ssh-keygen"):
        rprint(
            f"  [yellow]-- ssh-keyscan/ssh-keygen not found; skipping host trust for {host}.[/]"
        )
        rprint(
            "     [italic]Install OpenSSH or use an https:// remote to avoid an interactive prompt.[/]"
        )
        return

    # 1. Check if host is already known
    if run_and_wait(
        ["ssh-keygen", "-F", host], capture_output=True, suppress_error=True
    ):
        return  # Host is known

    # 2. If not known, scan and add keys
    rprint(f"  [yellow]-- Trusting new host: {host}[/]")
    ssh_dir = os.path.expanduser("~/.ssh")
    if not os.path.exists(ssh_dir):
        # Unix permission bits are a no-op on Windows (NTFS ACLs), so only set
        # mode on POSIX where it matters.
        if platform.IS_WINDOWS:
            os.makedirs(ssh_dir, exist_ok=True)
        else:
            os.makedirs(ssh_dir, mode=0o700)

    try:
        result = subprocess.run(
            ["ssh-keyscan", "-H", host],
            capture_output=True,
            check=True,
            text=True,
            encoding="utf-8",
            errors="replace",
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


def to_argv(cmd):
    """Normalize a command into an argv list for shell-free execution.

    Accepts a list/tuple (used as-is, the preferred form) or a string (tokenized
    with POSIX rules on every platform). POSIX tokenization strips shell quoting
    such as the single quotes around a kubectl ``jsonpath=`` value and yields the
    tokens the tool should receive as argv. Genuine shell pipelines must NOT be
    passed here -- they are handled explicitly by their callers (e.g. two-step
    apply, Popen streaming). Path arguments should already be forward-slashed via
    platform.posix_path/to_mount_path so POSIX tokenization never eats a Windows
    backslash.
    """
    if isinstance(cmd, (list, tuple)):
        return [str(part) for part in cmd]
    return shlex.split(cmd, posix=True)


def run_and_wait(
    cmd,
    capture_output=True,
    check_result="",
    cwd=None,
    suppress_error=False,
    _retry_count=0,
) -> int:
    """Run a command (no shell) and wait for it to finish.

    ``cmd`` may be an argv list (preferred) or a string that gets tokenized.
    Returns 1 on success (or when ``check_result`` is found in stdout), else 0.
    """

    # Local vars
    found = 0
    argv = to_argv(cmd)

    # Run the command and return the output
    try:
        output = subprocess.run(
            argv,
            capture_output=capture_output,
            shell=False,
            check=True,
            cwd=cwd,  # Allow running in specific directory
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        if check_result:
            results = (output.stdout or "").splitlines()
            for line in results:
                if re.search(check_result, line):
                    found = 1

            # Returning either that the check was successful (if there was a check)
            # or that the command was successful (if there wasn't a check)
            return found

        # Get to this point implies success
        return 1

    except FileNotFoundError:
        # The executable isn't installed / not on PATH.
        if not suppress_error:
            missing = argv[0] if argv else str(cmd)
            rprint(f"\n[red]Command not found:[/red] {missing}")
        return 0

    except CalledProcessError as error:
        # stderr is already text because we run with text=True
        err_text = error.stderr or ""
        is_kubectl = bool(argv) and "kubectl" in os.path.basename(argv[0])
        if is_kubectl and (
            "connection refused" in err_text or "server was refused" in err_text
        ):
            if _retry_count < 3:
                # Attempt to fix connectivity by refreshing kubeconfig.
                # We use subprocess directly to avoid recursion loops.
                subprocess.run(
                    [
                        platform.k3d_bin(),
                        "kubeconfig",
                        "merge",
                        "k3s-default",
                        "--kubeconfig-switch-context",
                    ],
                    shell=False,
                    capture_output=True,
                    check=False,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
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


def run_and_return(cmd) -> str:
    """Run a command (no shell) and return its stdout as a string"""

    # Run the command and return the output
    try:
        output = subprocess.run(
            to_argv(cmd),
            capture_output=True,
            shell=False,
            check=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return (output.stdout or "").strip()
    except (CalledProcessError, FileNotFoundError):
        return ""


def verify_pod_is_installed(pod: str) -> bool:
    """Verify there is still a pod in the cluster"""

    # Get the full name of the pod
    pod_name = get_full_pod_name(pod)

    # If we found a pod by name or we see it in the kubectl get pods command
    # the pod is still "installed" in k3s
    return pod_name or run_and_wait("""kubectl get pods""", check_result=pod)


def verify_cluster_connection(retries=10) -> bool:
    """Verify that kubectl can connect to the cluster"""
    cmd = ["kubectl", "cluster-info"]
    for _ in range(retries):
        try:
            # We use subprocess directly (not run_and_wait) to avoid loop
            # recursion logging / auto-heal during this readiness poll.
            subprocess.run(cmd, capture_output=True, shell=False, check=True)
            return True
        except (CalledProcessError, FileNotFoundError):
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
        cmd = ["kubectl", "get", "pods", "--all-namespaces"]

        try:
            results = subprocess.run(
                cmd,
                capture_output=True,
                shell=False,
                check=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            # Look for the pod and the status to see if it's ready
            for line_str in (results.stdout or "").splitlines():
                if re.search(podname, line_str) and re.search(status, line_str):
                    pod_complete = 1
        except (CalledProcessError, FileNotFoundError):
            pass

        cycles += 1
        sleep(0.5)

    return bool(pod_complete)


def wait_for_mysql_socket(retries=30) -> bool:
    """Wait for MySQL socket to be available inside the pod"""
    pod_name = get_full_pod_name("mysql").strip("\n")
    if not pod_name:
        return False

    for _ in range(retries):
        # We use a real query to test connectivity, not just admin ping
        cmd = [
            "kubectl",
            "exec",
            pod_name,
            "--",
            "mysql",
            "-uroot",
            "-ppassword",
            "-e",
            "SELECT 1",
        ]
        try:
            subprocess.run(cmd, capture_output=True, shell=False, check=True)
            return True
        except CalledProcessError:
            sleep(1)
    return False


def wait_for_postgres_socket(retries=30) -> bool:
    """Wait for Postgres socket to be available inside the pod"""
    pod_name = get_full_pod_name("postgres").strip("\n")
    if not pod_name:
        return False

    for _ in range(retries):
        # We use a real query to test connectivity
        cmd = [
            "kubectl",
            "exec",
            pod_name,
            "--",
            "psql",
            "-U",
            "root",
            "-d",
            "postgres",
            "-c",
            "SELECT 1",
        ]
        try:
            subprocess.run(cmd, capture_output=True, shell=False, check=True)
            return True
        except CalledProcessError:
            sleep(1)
    return False


def create_postgres_database(database, retries=0):
    """Create a database inside postgres"""
    # We check (and create) the DB with a small pipeline that runs INSIDE the
    # Linux container via `sh -c`, so it stays POSIX even on a Windows host.
    # This prevents Postgres from throwing errors on subsequent "auto start" runs.
    pipeline = f"psql -U root -lqt | grep -qw {database} || createdb -U root {database}"
    pod_name = get_full_pod_name("postgres").strip("\n")

    if pod_name:
        cmd = ["kubectl", "exec", pod_name, "--", "sh", "-c", pipeline]

        try:
            # Run the command silently
            subprocess.run(
                cmd,
                shell=False,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except CalledProcessError:
            if retries < 10:  # Allow up to 30s for slower startups
                sleep(3)
                create_postgres_database(database, retries=retries + 1)
            else:
                rprint(f"  [red]FAILED: Could not create database[/] {database}")

    else:
        # If pod_name not found, wait and retry
        if retries < 10:
            sleep(3)
            create_postgres_database(database, retries=retries + 1)
        else:
            rprint(f"  [red]FAILED: Could not create database[/] {database}")


def get_full_pod_name(pod) -> str:
    """Get the name of the first Running k3s pod matching an application name.

    Replaces the old `kubectl get pods | grep | grep Running | awk` pipeline with
    JSON parsing so it works without a POSIX shell on any platform.
    """

    output = run_and_return(["kubectl", "get", "pods", "-o", "json"])
    if not output:
        return ""

    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return ""

    for item in data.get("items", []):
        name = item.get("metadata", {}).get("name", "")
        phase = item.get("status", {}).get("phase", "")
        if pod in name and phase == "Running":
            return name

    return ""


def connect_to_db() -> None:
    """Open an interactive MySQL shell inside the cluster's mysql pod"""

    pod_name = get_full_pod_name("mysql").strip("\n")
    cmd = ["kubectl", "exec", "-it", pod_name, "--", "mysql", "-uroot", "-ppassword"]

    # Interactive: inherit the parent stdio so the TTY works
    subprocess.run(cmd, shell=False, check=True)


def connect_to_db_postgres() -> None:
    """Open an interactive psql shell inside the cluster's postgres pod"""

    pod_name = get_full_pod_name("postgres").strip("\n")
    cmd = ["kubectl", "exec", "-it", pod_name, "--", "psql", "-U", "root", "postgres"]

    # Interactive: inherit the parent stdio so the TTY works
    subprocess.run(cmd, shell=False, check=True)


def connect_to_minio() -> None:
    """This opens the port-forward to MinIO to allow dev access"""

    pod_name = get_full_pod_name("minio").strip("\n")
    cmd = ["kubectl", "port-forward", pod_name, "9090:9090"]

    # Long-running: inherit the parent stdio so Ctrl+C reaches it
    subprocess.run(cmd, shell=False, check=True)


def create_mysql_database(database, retries=0):
    """Create a database inside mysql"""

    # IF NOT EXISTS prevents a failed retry loop when the database already exists
    pod_name = get_full_pod_name("mysql").strip("\n")

    if pod_name:
        cmd = [
            "kubectl",
            "exec",
            pod_name,
            "--",
            "mysql",
            "-uroot",
            "-ppassword",
            f"--execute=CREATE DATABASE IF NOT EXISTS {database}",
        ]

        try:
            # Run the command silently.
            # We capture output to suppress "ERROR 2002" messages during startup.
            subprocess.run(
                cmd,
                shell=False,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except CalledProcessError:
            if retries < 10:  # Allow up to 30s for slower startups
                sleep(3)
                create_mysql_database(database, retries=retries + 1)
            else:
                rprint(f"  [red]FAILED: Could not create database[/] {database}")

    else:
        # If pod_name not found, wait and retry
        if retries < 10:
            sleep(3)
            create_mysql_database(database, retries=retries + 1)
        else:
            rprint(f"  [red]FAILED: Could not create database[/] {database}")


def create_minio_bucket(bucket):
    """Create a bucket in MinIO"""

    pod_name = get_full_pod_name("minio").strip("\n")

    if pod_name:
        # Batch all three mc commands into a single exec call to avoid subprocess overhead per bucket
        combined = (
            f"mc mb --quiet myminio/{bucket} ; "  # disable file list
            f"mc anonymous --quiet set none myminio/{bucket} && "  # enable full path access
            f"mc anonymous --quiet set download myminio/{bucket}/*"
        )
        # The combined pipeline runs INSIDE the Linux container via `sh -c`,
        # passed as a single argv element so no host shell is involved.
        cmd = ["kubectl", "exec", pod_name, "--", "sh", "-c", combined]
        subprocess.run(
            cmd,
            shell=False,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )


def check_docker():
    """Make sure docker exists and the daemon is reachable"""

    # Error count
    errors = 0

    # Verify docker is installed (cross-platform PATH lookup; no `which`)
    if not platform.which("docker"):
        declare_error(
            """Docker is missing!
               [yellow]We didn't see docker on your system.  You'll need docker installed to continue""",
            exit_auto=False,
        )
        # No point checking the daemon if the client isn't even installed
        return errors + 1

    # Verify the daemon is reachable. `docker info` works the same on Linux,
    # macOS and Windows (Docker Desktop / Rancher Desktop) and does not depend on
    # a host `dockerd` process (there isn't one on Windows).
    if not run_and_wait(["docker", "info"], capture_output=True, suppress_error=True):
        if platform.IS_WINDOWS or platform.IS_MAC:
            msg = """The Docker engine doesn't appear to be running.
        Please start Docker Desktop (or Rancher Desktop) and wait for it to be ready."""
        else:
            msg = """The Docker daemon doesn't appear to be running.
        Please run the following command:
          `sudo service docker start`"""
        declare_error(msg, exit_auto=False)
        errors += 1

    return errors


def check_k8s():
    """Look for the things necessary to run k3s via k3d"""

    # Error count
    errors = 0

    # check for the k3d command
    bash_command = """k3d cluster list"""
    if not run_and_wait(bash_command, check_result="LOADBALANCER"):
        declare_error(
            """The `k3d` command doesn't appear to be installed!
             Please visit https://k3d.io for installation instructions.
          """,
            exit_auto=False,
        )
        errors += 1

    # check for the kubectl command
    bash_command = """kubectl get --help"""
    if not run_and_wait(bash_command, check_result="Display one or many resources"):
        declare_error(
            """The `kubectl` command doesn't appear to be installed!
             Please install it to continue.
          """,
            exit_auto=False,
        )
        errors += 1

    return errors


def check_helm():
    """Look for the things necessary to run helm"""

    # Error count
    errors = 0

    # check for the helm command
    bash_command = """helm version"""
    if not run_and_wait(bash_command, check_result="clean"):
        declare_error(
            """The `helm` command doesn't appear to be installed!
             Please visit https://helm.sh/docs/intro/install/ for installation instructions.
          """,
            exit_auto=False,
        )
        errors += 1

    return errors


def check_registry_host_entry():
    """Check that appropriate host entries are made"""

    # Error count
    errors = 0

    # check for the k3d-registry.local host entry
    if not check_host_entry("k3d-registry", exit_auto=False):
        errors += 1

    return errors


def check_host_entry(host, exit_auto: bool = True):
    """Check that a host entry for the pod has been made"""

    # Read the system hosts file directly in Python (no `cat`; correct path
    # per-OS). On a locked-down Windows box this may come back empty, so we also
    # fall back to DNS resolution below.
    if re.search(host, platform.read_hosts()):
        return True

    # Fall back to DNS resolution: the mapping might live somewhere other than
    # the hosts file, or the hosts file may be unreadable without elevation.
    try:
        socket.gethostbyname(f"{host}.local")
        return True
    except OSError:
        pass

    declare_error(
        f"""No registry entry in the hosts file !
       Please add the following line to {platform.hosts_path()}
       127.0.0.1      {host}.local
          """,
        exit_auto=exit_auto,
    )

    return False


# winget package ids for the tools auto can auto-install on Windows.
# k3d has no reliable winget package, so it is handled with manual guidance.
_WINGET_IDS = {
    "docker": "Docker.DockerDesktop",
    "kubectl": "Kubernetes.kubectl",
    "helm": "Helm.Helm",
    "git": "Git.Git",
    "mkcert": "FiloSottile.mkcert",
}


def run_doctor(fix=False):
    """Report (and optionally install) the external tools auto depends on.

    This is the in-CLI prerequisite checker. On Windows, ``--fix`` installs the
    winget-available tools; k3d is guided manually (scoop/choco/download). For a
    full automated setup, the PowerShell installer's ``-InstallDeps`` is richer.
    """
    https = CONFIG.get("https", False)

    # (tool, note). k3d intentionally has no winget id (see _WINGET_IDS).
    tools = [
        ("docker", "Docker Desktop provides the engine k3d runs on"),
        ("k3d", "install via: scoop install k3d | choco install k3d | https://k3d.io"),
        ("kubectl", ""),
        ("helm", "optional, only needed for helm charts"),
        ("git", ""),
    ]
    if https:
        tools.append(("mkcert", "only needed when https: true"))

    rprint("[deep_sky_blue1 bold]auto doctor[/]\n")
    missing = []
    for name, note in tools:
        if platform.which(name):
            rprint(
                f"  {name:<8} [green]:white_heavy_check_mark: found[/]"
                + (f"  [italic]{note}[/]" if note else "")
            )
        else:
            rprint(
                f"  {name:<8} [red]:x: missing[/]"
                + (f"  [italic]{note}[/]" if note else "")
            )
            missing.append(name)

    # Docker daemon reachability (only meaningful if the client is present)
    if platform.which("docker"):
        if run_and_wait(["docker", "info"], capture_output=True, suppress_error=True):
            rprint("  engine   [green]:white_heavy_check_mark: docker running[/]")
        else:
            rprint("  engine   [yellow]not reachable -- start Docker Desktop[/]")

    if not missing:
        rprint("\n[green]All required tools are present.[/]")
        return

    if fix and platform.IS_WINDOWS and platform.which("winget"):
        for name in missing:
            winget_id = _WINGET_IDS.get(name)
            if winget_id:
                rprint(f"\n[deep_sky_blue1]Installing {name} via winget...[/]")
                run_and_wait(
                    [
                        "winget",
                        "install",
                        "-e",
                        "--id",
                        winget_id,
                        "--accept-source-agreements",
                        "--accept-package-agreements",
                    ],
                    capture_output=False,
                )
            else:
                rprint(
                    f"\n[yellow]{name} must be installed manually (see note above).[/]"
                )
        rprint("\n[italic]Open a new terminal so PATH changes take effect.[/]")
    else:
        rprint(
            "\n[yellow]Some tools are missing.[/] On Windows, re-run with "
            "[bright_cyan]--fix[/] to install via winget,"
        )
        rprint(
            "or run the installer's setup once: "
            "[bright_cyan]install_auto.ps1 -InstallDeps[/]"
        )


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

        # `git pull` the repo
        cmd = f"git pull {repo['repo']}"
        if not run_and_wait(cmd):
            rprint(f"[yellow]       :warning: Skipping {repo['repo']}")

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


def setup_minio(retries=5):
    """Setup the credentials and configure and deploy nginx"""

    container_cmds = [
        "mc alias -q set myminio http://minio.default.svc.cluster.local:9000 minio minio123"
    ]
    pod_name = get_full_pod_name("minio").strip("\n")

    if pod_name:
        # Let's run the commands in the container to setup the access creds
        for container_cmd in container_cmds:
            # container_cmd is a fixed, space-delimited command; tokenize it and
            # exec it inside the pod with no host shell involved.
            cmd_with_args = ["kubectl", "exec", "-it", pod_name, "--"] + shlex.split(
                container_cmd
            )

            # Run the command and return the output
            subprocess.run(
                cmd_with_args,
                shell=False,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
            )

    else:
        if retries > 1:
            sleep(3)
            setup_minio(retries - 1)


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
    if run_and_wait("k3d cluster list", check_result="NAME"):
        # Check if running (1/1 servers running)
        if run_and_wait("k3d cluster list", check_result="1/1"):
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


def check_certutil():
    """Check for the NSS certutil that mkcert needs on Linux.

    On Windows, mkcert uses the system certificate store directly -- the built-in
    Windows ``certutil.exe`` is a different tool and is NOT required -- so we skip
    this check there to avoid a false positive. macOS uses the system keychain.
    """
    if platform.IS_WINDOWS:
        return
    if not shutil.which("certutil"):
        declare_error(
            "certutil is not installed (required for mkcert).\n"
            "  Please install it:\n"
            "  - Ubuntu/Debian: sudo apt install libnss3-tools\n"
            "  - Fedora: sudo dnf install nss-tools\n"
            "  - Arch: sudo pacman -S nss\n"
            "  - macOS: brew install nss"
        )


def check_mkcert():
    """Check if mkcert is installed"""
    if not shutil.which("mkcert"):
        declare_error(
            "mkcert is not installed. Please install it to use HTTPS.\n"
            "    See: https://github.com/FiloSottile/mkcert"
            "Or set `HTTPS: false` in `~/.auto/config/local.yaml`"
        )
    # Also check for certutil so we don't fail partially
    check_certutil()


def create_local_certs(cert_path, additional_domains=None):
    """Create local certificates using mkcert"""

    if additional_domains is None:
        additional_domains = []

    # Create the directory if it doesn't exist
    if not os.path.isdir(cert_path):
        os.makedirs(cert_path)

    key_file = os.path.join(cert_path, "key.pem")
    cert_file = os.path.join(cert_path, "cert.pem")

    # Install the local CA. Try silently first (succeeds if already installed or
    # no elevation is needed). On failure, run again with inherited stdio so a
    # sudo password (POSIX) or UAC dialog (Windows) can be handled by the user.
    try:
        subprocess.run(
            ["mkcert", "-install"],
            shell=False,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (CalledProcessError, FileNotFoundError):
        rprint("  -- Installing local CA (may prompt for elevation)")
        subprocess.run(["mkcert", "-install"], shell=False, check=False)

    # Generate the certs as an argv list so '*.local' needs no shell quoting
    # (single quotes are literal on cmd.exe). We suppress output unless it fails.
    cmd = [
        "mkcert",
        "-key-file",
        key_file,
        "-cert-file",
        cert_file,
        "*.local",
        "localhost",
        "127.0.0.1",
        "::1",
        *additional_domains,
    ]

    try:
        subprocess.run(
            cmd,
            shell=False,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except CalledProcessError as e:
        rprint("[red]Error generating certificates:[/red]")
        print(e.stderr or "")
    except FileNotFoundError:
        rprint("[red]mkcert not found; cannot generate certificates.[/red]")

    return key_file, cert_file


def get_deployment_spec(name, namespace="default"):
    """Fetch a deployment's spec as a dict, or None if it doesn't exist"""
    cmd = ["kubectl", "get", "deployment", name, "-n", namespace, "-o", "json"]
    try:
        result = subprocess.run(
            cmd,
            shell=False,
            capture_output=True,
            check=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return json.loads(result.stdout)
    except (CalledProcessError, json.JSONDecodeError, FileNotFoundError):
        return None


def _build_runner_pod_manifest(
    pod_name,
    runner_name,
    action_label,
    command_args,
    deployment,
    extra_env,
    namespace,
):  # pylint: disable=too-many-arguments
    """Build a Pod manifest mirroring deployment's first container, with overrides"""
    spec_template = deployment["spec"]["template"]["spec"]
    container = spec_template["containers"][0]

    env_list = list(container.get("env", []))
    if extra_env:
        env_list.extend(extra_env)

    pod_spec = {
        "restartPolicy": "Never",
        "containers": [
            {
                "name": action_label,
                "image": container["image"],
                "command": list(command_args),
                "workingDir": container.get("workingDir", f"/mnt/code/{pod_name}"),
                "env": env_list,
                "envFrom": container.get("envFrom", []),
                "volumeMounts": container.get("volumeMounts", []),
            }
        ],
        "volumes": spec_template.get("volumes", []),
    }
    if "serviceAccountName" in spec_template:
        pod_spec["serviceAccountName"] = spec_template["serviceAccountName"]
    if "imagePullSecrets" in spec_template:
        pod_spec["imagePullSecrets"] = spec_template["imagePullSecrets"]

    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": runner_name,
            "namespace": namespace,
            "labels": {
                "app.kubernetes.io/managed-by": "auto",
                "auto.devocho/role": action_label,
                "auto.devocho/target": pod_name,
            },
        },
        "spec": pod_spec,
    }


def run_one_shot_pod_command(
    pod_name,
    command_args,
    action_label,
    extra_env=None,
    namespace="default",
):
    """Run command_args in an ephemeral pod that mirrors pod_name's deployment.

    Spawns a fresh Pod using the same image, env, envFrom, volumeMounts, and
    volumes as the application Deployment, but overrides the command. This
    avoids depending on the application container being healthy — the right
    behavior for migrations, db init, and seed scripts that should run even
    if the app pod is CrashLooping.

    Returns 0 on Pod phase Succeeded, 1 otherwise.
    """
    deployment = get_deployment_spec(pod_name, namespace)
    if not deployment:
        declare_error(
            f"Deployment '{pod_name}' not found in namespace '{namespace}'. "
            f"Run 'auto start {pod_name}' first to install it."
        )
        return 1

    # Unique pod name so concurrent runs and old failed migrators don't collide
    runner_name = f"{pod_name}-{action_label}-{uuid.uuid4().hex[:8]}"
    pod_manifest = _build_runner_pod_manifest(
        pod_name,
        runner_name,
        action_label,
        command_args,
        deployment,
        extra_env,
        namespace,
    )
    manifest_yaml = yaml.safe_dump(pod_manifest)

    try:
        rprint(f"  -- Spawning {action_label} pod for {pod_name}")
        result = subprocess.run(
            ["kubectl", "apply", "-f", "-"],
            shell=False,
            input=manifest_yaml,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode != 0:
            rprint(f"[red]Failed to create {action_label} pod[/red]")
            print(result.stderr)
            return 1

        # Short timeout — if the migrator can't even schedule, something
        # structural is wrong (missing image, bad envFrom secret, etc.)
        sched_cmd = (
            f"kubectl wait --for=condition=PodScheduled "
            f"pod/{runner_name} -n {namespace} --timeout=60s"
        )
        if not run_and_wait(sched_cmd, capture_output=True, suppress_error=True):
            rprint(
                f"[red]{action_label} pod failed to schedule "
                f"— describe output:[/red]"
            )
            run_and_wait(
                f"kubectl describe pod/{runner_name} -n {namespace}",
                capture_output=False,
            )
            return 1

        # Stream logs until the container exits. Inheriting stdio (no capture)
        # lets the user see output in real time without a shell or buffering.
        rprint(f"  -- Streaming {action_label} output for {pod_name}")
        subprocess.run(
            ["kubectl", "logs", "-f", f"pod/{runner_name}", "-n", namespace],
            shell=False,
            check=False,
        )

        # Pod has exited (or user Ctrl-C'd). Check the actual phase rather
        # than trusting the log stream's exit code. The jsonpath value is passed
        # as a single argv element, so no shell quoting is needed.
        phase_result = subprocess.run(
            [
                "kubectl",
                "get",
                f"pod/{runner_name}",
                "-n",
                namespace,
                "-o",
                "jsonpath={.status.phase}",
            ],
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        phase = phase_result.stdout.strip().strip("'")

        if phase == "Succeeded":
            rprint(f"  -- [green]{action_label} for {pod_name} completed[/green]")
            return 0

        rprint(
            f"  -- [red]{action_label} for {pod_name} ended in phase "
            f"{phase or 'unknown'}[/red]"
        )
        return 1
    finally:
        # Always clean up so a leaked migrator doesn't block a retry
        run_and_wait(
            f"kubectl delete pod/{runner_name} -n {namespace} " f"--ignore-not-found",
            capture_output=True,
            suppress_error=True,
        )
