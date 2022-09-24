"""Utils for the auto commands"""

import re
import shlex
import subprocess
import sys
from time import sleep

from rich import print as rprint


def run_and_wait(cmd: str, check_result="") -> int:
    """Run a Bash command and wait for it to finish"""

    # Local vars
    found = 0

    # Make this command safe to run
    cmd = shlex.quote(cmd)
    args = shlex.split(cmd)

    # Run the command and return the output
    output = subprocess.run(args, capture_output=True, shell=True, check=True)

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


def wait_for_pod_status(podname: str, status: str, max_wait_time=30) -> None:
    """Check for a pod to be complete and then return"""

    # Local vars
    pod_complete = 0
    cycles = 0

    while not pod_complete or cycles < max_wait_time:
        # Get the pod(s) in question
        bash_command = f"""kubectl get pods --all-namespaces | grep {podname}"""
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
        sleep(1)


def get_full_pod_name(pod) -> str:
    """Get the full name of the pod for a k3s pod by application name"""

    cmd = (
        f"kubectl get pods --selector=app={pod} "
        + "| grep Running | awk 'NR==1{{print $1}}'"
    )

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
