"""Imports"""
import os
import re
import sys
import time

import requests
import toml
from autocli import utils
from rich import print as rprint

# GLOBALS
CONFIG = toml.load(os.path.expanduser("~") + "/.auto/config/local.toml", _dict=dict)


def check_docker():
    """Make sure docker exists and the service is running"""

    rprint("  -- Is Docker installed on the system?")
    # Verify docker is installed
    bash_command = """which docker"""
    if not utils.run_and_wait(bash_command, check_result="/usr/bin/docker"):
        rprint(
            """[red]ERROR: Uh Oh!
                We didn't see docker on your system.  You'll need that installed to continue"""
        )
        sys.exit()

    # Verify docker is running
    bash_command = """ps aux"""
    if not utils.run_and_wait(bash_command, check_result="dockerd"):
        rprint(
            """[red]      ERROR: Uh Oh! Docker Daemon doesn't appear to be running.[/]
        Please run the following command:
          `sudo service docker start`"""
        )
        sys.exit()


def start_registry(progress, task):
    """Start a container registry"""

    rprint("  -- Does a registry already exist?")
    # Do we have a registry or do we need to create one?
    bash_command = """/usr/local/bin/k3d registry list"""
    if utils.run_and_wait(bash_command, check_result="k3d-registry.local"):
        rprint("     = Found a registry so we will use it")
    else:
        # No registry found so we need to make one
        bash_command = """k3d registry create registry.local --port 12345"""
        utils.run_and_wait(bash_command)
        rprint("    [steel_blue1]Created Registry")
        time.sleep(3)
    progress.update(task, advance=5)


def populate_registry(progress, task):
    """Load important images into the registry to speed up the deployment of pods"""

    # Get the list of images we want to pre-load from the config file
    registry_load_list = CONFIG["images"]["images"]

    # Get the registry items already loaded
    req = requests.get("http://k3d-registry.local:12345/v2/_catalog")
    registry_list = req.json()

    # Remove loaded items from the list
    for image in registry_list["repositories"]:
        for delete_candidate in registry_load_list:
            candidate_name = delete_candidate.split(":")[0]
            if re.search(candidate_name, image):
                registry_load_list.remove(delete_candidate)

    # Load the items we haven't already got in the registry
    for image in registry_load_list:

        # Is this a local image already?
        bash_command = "docker images"
        if not utils.run_and_wait(bash_command, check_result=image.split(":")[0]):
            bash_command = "docker pull " + image
            utils.run_and_wait(bash_command)

        # Let's tag the image for the registry
        bash_command = "docker tag " + image + " k3d-registry.local:12345/" + image
        utils.run_and_wait(bash_command)

        # Push the image into the registry
        bash_command = "docker push k3d-registry.local:12345/" + image
        utils.run_and_wait(bash_command)
        rprint("  -- Loaded: [steel_blue]" + image)
    progress.update(task, advance=5)


def start_cluster(progress, task):
    """Start a K3D cluster and return if it is new (true) or existing (false)"""

    # Verify the cluster isn't already running
    rprint("  -- Does a cluster already exist?")
    bash_command = """/usr/local/bin/k3d cluster list"""
    if utils.run_and_wait(bash_command, check_result="k3s-default"):
        print("     = cluster exists so using it")

        # Is the cluster running or stopped?
        if utils.run_and_wait(bash_command, check_result="0/1"):
            print("         - Uh oh, it's not running... starting it now")
            bash_command = """k3d cluster start"""
            utils.run_and_wait(bash_command)

        return False

    # The cluster hasn't already been created so I need to start one
    progress.update(task, advance=5)
    print("       = Nope, creating cluster.  This will take a minute.")
    # if it's not running let's start it
    load_bal_port = 8088
    code_dir = CONFIG["code"]["directory"]
    bash_command = f"""/usr/local/bin/k3d cluster create \
                       --volume {code_dir}:/mnt/code \
                       --registry-use k3d-registry.local:12345 \
                       --registry-config ~/.auto/k3s/registries.yaml \
                       --api-port 6550 -p "{load_bal_port}:80@loadbalancer" \
                       --agents 1"""

    # We want to let the k3d pods finish running so we can remove the temporary ones
    if utils.run_and_wait(bash_command):
        print("       = Cluster Started.  Waiting for Pods to finish starting...")
        progress.update(task, advance=6)

    if utils.wait_for_pod_status("helm", "Complete"):
        progress.update(task, advance=5)

    # We run this twice because there are two pods
    if utils.wait_for_pod_status("helm", "Complete"):
        progress.update(task, advance=6)

    # Let's remove the completed helm containers
    bash_command = """kubectl delete pod -n kube-system \
                      --field-selector=status.phase==Succeeded"""
    if utils.run_and_wait(bash_command):
        print("       = Pods finished starting.  Removed completed setup pods.")

    return True


def stop_cluster(progress, task) -> None:
    """Stop the cluster"""

    print("  -- Stopping cluster")
    bash_command = """/usr/local/bin/k3d cluster stop"""
    utils.run_and_wait(bash_command)
    progress.update(task, advance=50)


def delete_cluster(progress, task) -> None:
    """Delete the cluster"""

    print("  -- Deleting cluster")
    bash_command = """/usr/local/bin/k3d cluster delete"""
    utils.run_and_wait(bash_command)
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


def start_pod(pod) -> None:
    """Start a single pod"""

    user_path = os.path.expanduser("~")

    for config_pod, values in CONFIG["pods"].items():
        if pod == values["name"]:
            # is this pod already running?
            if utils.run_and_wait("""kubectl get pods""", check_result=values["name"]):
                rprint(f"       * {config_pod}: [steel_blue]already running")

            # If we aren't running let's start via helm
            else:
                command = f"""helm install {values['command_args']} """
                command += f"""--description \"{values['desc']}\" """
                command += f"""{values['name']} {user_path}/.auto/helm/{values['helm_directory']}"""
                utils.run_and_wait(command)
                rprint(f"       * [steel_blue]Helm installed: [/]{pod}")


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
    """Install Portals into the cluster"""

    print("  -- Installing Portals")
    # Let's setup the code directory PV and PVC in k3s
    user_path = os.path.expanduser("~")
    command = f"kubectl apply -f {user_path}/.auto/k3s/pv.yaml"
    utils.run_and_wait(command)
    command = f"kubectl apply -f {user_path}/.auto/k3s/pvc.yaml"
    utils.run_and_wait(command)
    rprint("        [green]Setup code directory in k3s")

    rprint("     = Portals:")
    for values in CONFIG["pods"].items():
        for pod in values:
            start_pod(pod)
            # init_pod_db(pod)


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

    # Let's give it a few seconds to start before we let other pods start
    time.sleep(5)
    rprint("       [green]Started MySQL")


def create_databases():
    """Create the databases"""
    rprint("  -- Creating Databases")

    # Let's create the mysql databases
    for database in CONFIG["mysql"]["databases"]:
        utils.create_mysql_database(database)
        print(f"      *  [steel_blue]Created database:[/] {database}")


def connect_to_mysql() -> None:
    """Connect to the MySQL cluster inside the k3s cluster"""

    utils.connect_to_db()


def tag_pod_docker_image(pod, version) -> None:
    """Tag and push a new docker image to local registry"""

    rprint(f"[steel_blue]Building and Tagging: [/][bright_cyan]{pod} {version}")

    # Verify the pod is real using the users source code folder
    code_path = os.environ["AUTO_CODE"]
    code_path = CONFIG["pods"][pod]["code_path"]
    rprint(f"code_path: {code_path}")
    if os.path.isdir(code_path + "/" + pod):
        rprint(f"  -- Found pod {pod}")

        # Perform docker build
        rprint(f"  -- Building {pod} container")
        command = f"docker build -t {pod}:{version} {code_path}/{pod}"
        utils.run_and_wait(command)

        # Tag the image for the registry
        rprint(f"  -- Tagging {pod} image for the registry")
        command = f"docker tag {pod}:{version} k3d-registry.local:12345/{pod}:{version}"
        utils.run_and_wait(command)

        # Push the image to the registry
        rprint(f"  -- Pushing {pod} image to the registry")
        command = f"docker push k3d-registry.local:12345/{pod}:{version}"
        utils.run_and_wait(command)

        # clean up your mess
        rprint("  -- Cleaning unused images")
        command = "docker image prune -f"
        utils.run_and_wait(command)

    # They tried to build a pod that didn't exist.  probably a typo
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

    # Let's see which pod we need to execute commands against
    pod_name = utils.get_full_pod_name(pod)

    # Setup the schema
    command = f"kubectl exec -ti {pod_name} -- ./seeddb.py"
    utils.run_and_wait(command)

    # Tell the user we did it
    rprint(f"  -- {pod} database seeded")


def init_pod_db(pod):
    """Run the initdb.py script inside a pod's container"""

    # Let's see which pod we need to execute commands against
    pod_name = utils.get_full_pod_name(pod)

    # Setup the schema
    command = f"kubectl exec -ti {pod_name} -- ./initdb.py"
    utils.run_and_wait(command)

    # Tell the user we did it
    rprint(f"  -- {pod} database initialized")
