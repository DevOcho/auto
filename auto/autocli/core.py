"""Imports"""
import os
import re
import time

import requests
import yaml
from autocli import utils
from rich import print as rprint

# Read Config and provide globally
CONFIG = {}
with open(
    os.path.expanduser("~") + "/.auto/config/local.yaml", encoding="utf-8"
) as yaml_file:
    CONFIG = yaml.safe_load(yaml_file)


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


def start_cluster(progress, task):
    """Start a K3D cluster and return if it is new (true) or existing (false)"""

    # Verify the cluster isn't already running
    bash_command = """/usr/local/bin/k3d cluster list"""
    if utils.run_and_wait(bash_command, check_result="k3s-default"):

        # Is the cluster running or stopped?
        if utils.run_and_wait(bash_command, check_result="0/1"):
            bash_command = """k3d cluster start"""
            utils.run_and_wait(bash_command)

        # Let them know this isn't a new cluster
        return False

    # The cluster hasn't already been created so I need to start one
    progress.update(task, advance=5)
    print("  -- Creating cluster (this will take a minute)")
    load_bal_port = 8088
    code_dir = CONFIG["code"]
    bash_command = f"""/usr/local/bin/k3d cluster create \
                       --volume {code_dir}:/mnt/code \
                       --registry-use k3d-registry.local:12345 \
                       --registry-config ~/.auto/k3s/registries.yaml \
                       --api-port 6550 -p "{load_bal_port}:80@loadbalancer" \
                       --agents 1"""

    # We want to let the k3d pods finish running so we can remove the temporary ones
    if utils.run_and_wait(bash_command):
        print("     = Cluster Started.  Waiting for Pods to finish starting...")
        progress.update(task, advance=6)

    # When we see "Complete" then we know it's done
    if utils.wait_for_pod_status("helm", "Complete"):
        progress.update(task, advance=5)

    # We run this twice because there are two pods
    if utils.wait_for_pod_status("helm", "Complete"):
        progress.update(task, advance=6)

    # Let's remove the completed helm containers
    bash_command = """kubectl delete pod -n kube-system \
                      --field-selector=status.phase==Succeeded"""
    if utils.run_and_wait(bash_command):
        print("     = Pods finished starting.  Removed completed setup pods.")

    # Tell them this is a new cluster
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

    # Local Vars
    os.path.expanduser("~")
    code_dir = CONFIG["code"]

    # If we get a dictionary we have to find the pod name from the repo name
    if isinstance(pod, dict):
        pod_name = pod["repo"].split("/")[-1:][0].replace(".git", "")
    else:
        pod_name = pod

    # Is this pod already running?
    if utils.run_and_wait("""kubectl get pods""", check_result=pod_name):
        rprint(f"       * {pod_name}: [steel_blue]already running")

    # If we aren't running let's start via helm install or kubectl apply
    else:
        # Get the pod config
        config_file_path = code_dir + "/" + pod_name + "/.auto/config.yaml"
        with open(config_file_path, encoding="utf-8") as pod_yaml:
            pod_config = yaml.safe_load(pod_yaml)

        # If they are using helm
        if re.search("helm", pod_config["command"]):
            if "command-args" in pod_config:
                command = f"""{pod_config['command']} {pod_config['command-args']} """
            else:
                command = f"""{pod_config['command']} """
            command += f"""--description \"{pod_config['desc']}\" """
            command += f"""{pod_config['name']} {code_dir}/{pod_name}/.auto/helm"""

        # They are using kubectl apply
        else:
            command = f"{pod_config['command']} {pod_config['command_args']}"

        # Run the pod install command
        utils.run_and_wait(command)
        rprint(f"     * [bright_cyan]: {pod_name}[/] installed")


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


def install_system_pods():
    """Install all of the system pods in the cluster"""

    # Local Vars
    pod_list = []

    # Get all the system pods that are active in the global config
    for pod in CONFIG["system-pods"]:
        if pod["active"]:
            if pod not in pod_list:
                pod_list.append(pod)

    # Now get the requested system pods from the pod configs
    for pod in CONFIG["pods"]:
        # Load the pod config to see what system pods it wants
        pod_name = pod["repo"].split("/")[-1:][0].replace(".git", "")
        pod_config = utils.get_pod_config(pod_name)
        for pod in pod_config["system-pods"]:
            if pod["name"] not in pod_list:
                pod_list.append(pod["name"])

    # Now let's start the ones that we found
    for pod in CONFIG["system-pods"]:
        rprint(f"[red]HERE: [/red]{pod}")
        if pod["name"] in pod_list:
            rprint(f"[red]HERE[/red]")
            rprint("  -- Starting: " + pod["name"])
            for command in pod["commands"]:
                try:
                    utils.run_and_wait(command)
                except Exception:  # pylint: disable=broad-except
                    rprint(f"    [red]Error running {command}")


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


def create_databases():
    """Create the databases"""
    rprint("  -- Creating Databases")

    # Let's create the mysql databases
    for system_pod in CONFIG["system-pods"]:
        if system_pod["name"] == "mysql":

            # Let's wait for the MySQL pod to start
            if utils.wait_for_pod_status("mysql", "Running"):
                rprint("       [green]MySQL running")
                # I haven't thought of a better way to handle this
                # The pod is running but I need to give MySQL a few seconds to
                # start inside the pod.  Probably should just keep trying to run
                # a command until it succeeds?
                time.sleep(5)

            # Create the databases requested in each of the pods
            for pod in CONFIG["pods"]:
                pod_name = pod["repo"].split("/")[-1:][0].replace(".git", "")
                pod_config = utils.get_pod_config(pod_name)
                for system_pod in pod_config["system-pods"]:
                    for database in system_pod["databases"]:
                        utils.create_mysql_database(database["name"])
                        rprint(f"      *  Created database: [bright_cyan]{database['name']}")


def connect_to_mysql() -> None:
    """Connect to the MySQL cluster inside the k3s cluster"""

    utils.connect_to_db()


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
    utils.run_and_wait(command, capture_output=False)

    # Tell the user we did it
    rprint(f"  -- {pod} database seeded")


def init_pod_db(pod):
    """Run the initdb.py script inside a pod's container"""

    # Let's see which pod we need to execute commands against
    pod_name = utils.get_full_pod_name(pod)

    # Setup the schema
    command = f"kubectl exec -ti {pod_name} -- ./initdb.py"
    utils.run_and_wait(command, capture_output=False)

    # Tell the user we did it
    rprint(f"  -- {pod} database initialized")


def verify_dependencies():
    """Verify the system has what it needs to run auto"""

    # Check for the docker daemon running and command being available
    utils.check_docker()

    # Check for k3d and kubectl
    utils.check_k8s()

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
