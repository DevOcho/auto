"""Utils for the auto commands"""

import configparser
import os
import re
import shlex
import subprocess
import sys
from subprocess import CalledProcessError
from time import sleep

import yaml
from rich import print as rprint


def load_config():
    """Load the global auto config"""

    # Local vars
    config = {}

    # Read Config and provide the data
    with open(
        os.path.expanduser("~") + "/.auto/config/local.yaml", encoding="utf-8"
    ) as yaml_file:
        config = yaml.safe_load(yaml_file)

    return config


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


def declare_error(error_msg: str):
    """Print an error message and exit"""

    rprint(f"\n [red]:x: Error[/red]: {error_msg}")
    sys.exit()


def run_and_wait(cmd: str, capture_output=True, check_result="") -> int:
    """Run a Bash command and wait for it to finish"""

    # Local vars
    found = 0

    # Make this command safe to run
    cmd = shlex.quote(cmd)
    args = shlex.split(cmd)

    # Run the command and return the output
    try:
        output = subprocess.run(
            args, capture_output=capture_output, shell=True, check=True
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

    except CalledProcessError:
        return 0


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


def wait_for_pod_status(podname: str, status: str, max_wait_time=60) -> None:
    """Check for a pod to be complete and then return"""

    # Local vars
    pod_complete = 0
    cycles = 0  # Each cycle is a half a second

    while not pod_complete or cycles < max_wait_time:
        # Get the pod(s) in question
        bash_command = f"""kubectl get pods --all-namespaces | grep {podname} || true"""
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

        cycles += 1
        sleep(0.5)


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


def create_mysql_database(database, retries=0):
    """Create a database inside mysql"""

    container_cmd = f'mysql -uroot -ppassword --execute="create database {database}"'
    pod_name = get_full_pod_name("mysql").strip("\n")

    if pod_name:
        cmd = f"kubectl exec -it {pod_name} -- {container_cmd}"

        # Make this command safe to run
        cmd = shlex.quote(cmd)
        args = shlex.split(cmd)

        # Run the command and return the output
        subprocess.run(args, shell=True, check=True)

    # If we don't get the pod_name then we need to wait and try again
    else:
        if retries:
            retries += 1
        else:
            retries = 1

        # We will try up to three times, waiting 3 seconds per time.
        if retries < 4:
            sleep(3)
            create_mysql_database(database, retries=retries)
        else:
            rprint(f"  [red]FAILED: Could not create database[/] {database}")


def check_docker():
    """Make sure docker exists and the service is running"""

    # Verify docker is installed
    bash_command = """which docker"""
    if not run_and_wait(bash_command, check_result="docker"):
        declare_error(
            """Docker is missing!
               [yellow]We didn't see docker on your system.  You'll need docker installed to continue"""
        )

    # Verify docker is running
    bash_command = """ps aux"""
    if not run_and_wait(bash_command, check_result="dockerd"):
        declare_error(
            """Docker Daemon doesn't appear to be running.
        Please run the following command:
          `sudo service docker start`"""
        )

    # Verify the `docker` command is available to this user
    bash_command = """docker ps"""
    if not run_and_wait(bash_command, check_result="CONTAINER ID"):
        declare_error(
            """The `docker` command doesn't appear to be working!
             Perhaps you need to run the post install steps:
               https://docs.docker.com/engine/install/linux-postinstall/
          """
        )


def check_k8s():
    """Look for the things necessary to run k3s via k3d"""

    # check for the k3d command
    bash_command = """k3d cluster list"""
    if not run_and_wait(bash_command, check_result="LOADBALANCER"):
        declare_error(
            """The `k3d` command doesn't appear to be installed!
             Please visit https://k3d.io for installation instructions.
          """
        )

    # check for the kubectl command
    bash_command = """kubectl get --help"""
    if not run_and_wait(bash_command, check_result="Display one or many resources"):
        declare_error(
            """The `kubectl` command doesn't appear to be installed!
             Please install it to continue.
          """
        )


def check_registry_host_entry():
    """Check that appropriate host entries are made"""

    # check for the k3d-registry.local host entry
    if not check_host_entry("k3d-registry"):
        sys.exit()


def check_host_entry(host):
    """Check that a host entry for the pod has been made"""

    # check for the k3d-registry.local host entry
    bash_command = """cat /etc/hosts"""
    if not run_and_wait(bash_command, check_result=host):
        declare_error(
            f"""No registry entry in /etc/hosts !
       Please add the following to your /etc/hosts file
       127.0.0.1      {host}.local
          """
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
