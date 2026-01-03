"""Utils for the auto commands"""

import configparser
import os
import re
import shlex
import shutil
import subprocess
import sys
from subprocess import CalledProcessError
from time import sleep

import yaml
from rich import print as rprint
from rich.prompt import Confirm
from rich.table import Table
from rich.text import Text


def load_config():
    """Load the global auto config"""

    # Local vars
    config = {}
    config_path = os.path.expanduser("~") + "/.auto/config/local.yaml"

    if not os.path.isfile(config_path):
        declare_error(
            "No local.yaml file available. Creating a default.", exit_auto=False
        )
        # Initialize a config file
        create_initial_config()

    # Read Config and provide the data
    with open(config_path, encoding="utf-8") as yaml_file:
        config = yaml.safe_load(yaml_file)

    # 1. Expand User and Variables in the code path
    if "code" in config:
        # Expands ~ to /home/user
        expanded_path = os.path.expanduser(config["code"])
        # Expands ${USER} or $HOME
        expanded_path = os.path.expandvars(expanded_path)
        config["code"] = expanded_path

        # 2. Check if folder exists, if not, ask to create
        if not os.path.exists(config["code"]):
            rprint(
                f"\n[yellow]Warning: Code folder '{config['code']}' does not exist.[/]"
            )
            if Confirm.ask("Do you want us to create it for you?"):
                try:
                    os.makedirs(config["code"])
                    rprint(f"[green]Created directory: {config['code']}[/]")
                except OSError as e:
                    declare_error(f"Could not create directory: {e}")
            else:
                declare_error("Code directory missing. Cannot proceed.")

    return config


def create_initial_config():
    """Create a default config file if none is present"""

    # We use ${HOME} here so the config file is portable if copied
    default_config = """
---
# The code folder is where you want us to download all of your pod code repositories
code: ${HOME}/source/devocho

# Each repo listed here will be run as a pod in k3s
pods:
  - repo: git@github.com:DevOcho/portal.git
    branch: main

# These are the system pods.  They use the config that comes with auto.
system-pods:
  - pod:
      name: mysql
      active: false
      commands:
        [
          "kubectl apply -f ~/.auto/k3s/mysql/pv.yaml",
          "kubectl apply -f ~/.auto/k3s/mysql/pvc.yaml",
          "kubectl apply -f ~/.auto/k3s/mysql/deployment.yaml",
          "kubectl apply -f ~/.auto/k3s/mysql/service.yaml",
        ]
      databases:
        - name: portal
"""

    if not os.path.isfile(os.path.expanduser("~") + "/.auto/config/local.yaml"):
        # Ensure the directory exists
        os.makedirs(os.path.expanduser("~") + "/.auto/config", exist_ok=True)

        with open(
            os.path.expanduser("~") + "/.auto/config/local.yaml", "w", encoding="utf-8"
        ) as config_file:
            config_file.write(default_config)


def ensure_host_known(git_url):
    """Ensure the git host is in known_hosts to prevent interactive prompts hanging"""
    # Extract domain from git@github.com:User/Repo.git or https://github.com/User/Repo
    domain_match = re.search(r"@(.*?):", git_url)
    if not domain_match:
        # fallback for https or other formats if needed, or return if no match
        return

    host = domain_match.group(1)

    # 1. Check if host is already known
    # ssh-keygen -F returns exit code 0 if found, 1 if not
    cmd_check = f"ssh-keygen -F {host}"
    if run_and_wait(cmd_check, capture_output=True, suppress_error=True):
        return  # Host is known

    # 2. If not known, scan and add keys
    rprint(f"  [yellow]-- Trusting new host: {host}[/]")
    ssh_dir = os.path.expanduser("~/.ssh")
    if not os.path.exists(ssh_dir):
        os.makedirs(ssh_dir, mode=0o700)

    # ssh-keyscan outputs the key, we append to known_hosts
    cmd_scan = f"ssh-keyscan -H {host} >> {ssh_dir}/known_hosts"

    # We use subprocess directly to handle the redirect easily
    try:
        subprocess.run(
            cmd_scan,
            shell=True,
            check=True,
            stderr=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
        )
        rprint(f"     [green]Host {host} added to known_hosts[/]")
    except CalledProcessError:
        rprint(
            f"     [red]Failed to automatically trust {host}. Git clone may fail.[/]"
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
    """Run a Bash command and wait for it to finish with retries"""

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


def wait_for_pod_status(podname: str, status: str, max_wait_time=60) -> None:
    """Check for a pod to be complete and then return"""

    # Local vars
    pod_complete = 0
    cycles = 0  # Each cycle is a half a second

    while not pod_complete and cycles < max_wait_time:
        # Get the pod(s) in question
        bash_command = f"""kubectl get pods --all-namespaces | grep {podname} || true"""
        results = subprocess.run(
            bash_command, capture_output=True, shell=True, check=True
        )

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


def wait_for_mysql_socket(retries=30) -> bool:
    """Wait for MySQL socket to be available inside the pod"""
    pod_name = get_full_pod_name("mysql").strip("\n")
    if not pod_name:
        return False

    for _ in range(retries):
        # We use a real query to test connectivity, not just admin ping
        cmd = f'kubectl exec {pod_name} -- mysql -uroot -ppassword -e "SELECT 1"'
        try:
            subprocess.run(cmd, capture_output=True, shell=True, check=True)
            return True
        except CalledProcessError:
            sleep(1)
    return False


def get_full_pod_name(pod) -> str:
    """Get the full name of the pod for a k3s pod by application name"""

    cmd = f"kubectl get pods | grep {pod} " + "| grep Running | awk 'NR==1{{print $1}}'"

    # Make this command safe to run
    cmd = shlex.quote(cmd)
    args = shlex.split(cmd)

    # Run the command and return the output
    pod_name = subprocess.run(args, capture_output=True, shell=True, check=True)

    # give the people what they want
    return pod_name.stdout.decode().strip("\n")


def connect_to_db() -> None:
    """Get the full name of the pod for a k3s pod by application name"""

    # The command we will send to the mysql pod
    container_cmd = "mysql -uroot -ppassword"

    # Determine which pod to exec against and build the command
    pod_name = get_full_pod_name("mysql").strip("\n")
    cmd = f"kubectl exec -it {pod_name} -- {container_cmd}"

    # Make this command safe to run
    cmd = shlex.quote(cmd)
    args = shlex.split(cmd)

    # Run the command and return the output
    subprocess.run(args, shell=True, check=True)


def connect_to_db_postgres() -> None:
    """Get the full name of the pod for a k3s pod by application name"""

    # The command we will send to the mysql pod
    container_cmd = "psql -U root postgres"

    # Determine which pod to exec against and build the command
    pod_name = get_full_pod_name("postgres").strip("\n")
    cmd = f"kubectl exec -it {pod_name} -- {container_cmd}"

    # Make this command safe to run
    cmd = shlex.quote(cmd)
    args = shlex.split(cmd)

    # Run the command and return the output
    subprocess.run(args, shell=True, check=True)


def connect_to_minio() -> None:
    """This opens the port-forward to MinIO to allow dev access"""

    # Determine which pod to exec against and build the command
    pod_name = get_full_pod_name("minio").strip("\n")

    # The command we are going to run
    cmd = f"kubectl port-forward {pod_name} 9000 9090"

    # Make this command safe to run
    cmd = shlex.quote(cmd)
    args = shlex.split(cmd)

    # Run the command and return the output
    subprocess.run(args, shell=True, check=True)


def create_mysql_database(database, retries=0):
    """Create a database inside mysql"""

    container_cmd = f'mysql -uroot -ppassword --execute="create database {database}"'
    pod_name = get_full_pod_name("mysql").strip("\n")

    if pod_name:
        cmd = f"kubectl exec -it {pod_name} -- {container_cmd}"

        # Make this command safe to run
        cmd = shlex.quote(cmd)
        args = shlex.split(cmd)

        try:
            # Run the command silently.
            # We capture output to suppress "ERROR 2002" messages during startup.
            subprocess.run(
                args,
                shell=True,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except CalledProcessError:
            if retries < 10:  # Increased retries to 10 (approx 30s) for slower startups
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

    container_cmds = [
        f"mc mb --quiet myminio/{bucket}",
        f"mc anonymous --quiet set none myminio/{bucket}",  # disable file list
        f"mc anonymous --quiet set download myminio/{bucket}/*",  # enable full path access
    ]
    pod_name = get_full_pod_name("minio").strip("\n")

    if pod_name:
        for cmd in container_cmds:
            cmd = f"kubectl exec -it {pod_name} -- {cmd}"

            # Make this command safe to run
            cmd = shlex.quote(cmd)
            args = shlex.split(cmd)

            # Run the command and return the output
            subprocess.run(
                args,
                shell=True,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
            )


def check_docker():
    """Make sure docker exists and the service is running"""

    # Error count
    errors = 0

    # Verify docker is installed
    bash_command = """which docker"""
    if not run_and_wait(bash_command, check_result="docker"):
        declare_error(
            """Docker is missing!
               [yellow]We didn't see docker on your system.  You'll need docker installed to continue""",
            exit_auto=False,
        )

        errors += 1

    # Verify docker is running
    bash_command = """ps aux"""
    if not run_and_wait(bash_command, check_result="dockerd"):
        declare_error(
            """Docker Daemon doesn't appear to be running.
        Please run the following command:
          `sudo service docker start`""",
            exit_auto=False,
        )
        errors += 1

    # Verify the `docker` command is available to this user
    bash_command = """docker ps"""
    if not run_and_wait(bash_command, check_result="CONTAINER ID"):
        declare_error(
            """The `docker` command doesn't appear to be working!
             Perhaps you need to run the post install steps:
               https://docs.docker.com/engine/install/linux-postinstall/
          """,
            exit_auto=False,
        )
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

    # check for the k3d-registry.local host entry
    bash_command = """cat /etc/hosts"""
    if not run_and_wait(bash_command, check_result=host):
        declare_error(
            f"""No registry entry in /etc/hosts !
       Please add the following to your /etc/hosts file
       127.0.0.1      {host}.local
          """,
            exit_auto=exit_auto,
        )

        return False

    # We found the entry so tell them everything is ok
    return True


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

    # Read Config and provide globally
    auto_config = load_config()
    config_file = auto_config["code"] + "/" + pod + "/.auto/config.yaml"

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
            full_cmd = f"kubectl exec -it {pod_name} -- {container_cmd}"

            # Make this command safe to run
            full_cmd = shlex.quote(full_cmd)
            cmd_with_args = shlex.split(full_cmd)

            # Run the command and return the output
            subprocess.run(
                cmd_with_args,
                shell=True,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
            )

    else:
        if retries > 1:
            sleep(3)
            setup_minio(retries - 1)


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
    """Check if libnss3-tools is installed"""
    if not shutil.which("certutil"):
        declare_error(
            "certutil is not installed (required for mkcert).\n"
            "  Please install it:\n"
            "  - Ubuntu/Debian: sudo apt install libnss3-tools\n"
            "  - Fedora: sudo dnf install nss-tools\n"
            "  - Arch: sudo pacman -S nss"
        )


def check_mkcert():
    """Check if mkcert is installed"""
    if not shutil.which("mkcert"):
        declare_error(
            "mkcert is not installed. Please install it to use HTTPS.\n"
            "  See: https://github.com/FiloSottile/mkcert"
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

    # Install the local CA
    # Try silently first (success if already installed or no sudo needed)
    try:
        subprocess.run(
            "mkcert -install",
            shell=True,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except CalledProcessError:
        # If silent fail, run interactively (likely needs sudo password)
        rprint("  -- Installing local CA (may prompt for password)")
        os.system("mkcert -install")

    # Generate the certs
    # We suppress output here unless it fails
    domain_args = " ".join(additional_domains)
    cmd = (
        f"mkcert -key-file {key_file} -cert-file {cert_file} "
        f"'*.local' localhost 127.0.0.1 ::1 {domain_args}"
    )

    try:
        subprocess.run(
            cmd,
            shell=True,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except CalledProcessError as e:
        rprint("[red]Error generating certificates:[/red]")
        print(e.stderr.decode())

    return key_file, cert_file
