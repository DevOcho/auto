"""Auto Commands

  * `--dry-run`     This is for automated testing and visually testing the output
  * `--offline`     This disables steps that require internet so you can work without Internet
"""

import click
from autocli import core, utils
from rich import print as rprint
from rich.progress import Progress

# Global settings for click
CONTEXT_SETTINGS = {
    "help_option_names": ["-h", "--help"],
    "ignore_unknown_options": True,
}


@click.group(context_settings=CONTEXT_SETTINGS)
@click.version_option(version="0.2.0")
def auto():
    """Commandline utility to assist with creating/deleting clusters and
    starting/stopping pods.

    This function is used to group all of the commands together.
    """


@auto.command()
@click.pass_context
@click.argument("pod", required=False)
@click.option("--dry-run", is_flag=True, default=False)
@click.option("--offline", is_flag=True, default=False)
def start(self, pod, dry_run, offline):  # pylint: disable=unused-argument
    """Start a new k3s/k3d cluster"""

    # Local Vars
    new_cluster = False
    pods = []

    if not pod:
        # Let's run the steps with a progress bar
        with Progress(transient=False) as progress:
            task = progress.add_task("Creating Dev Environment", total=100)

            # Are we ready?
            # STEP 1: Verify Docker is running on the host
            rprint("[deep_sky_blue1]Verify Dependencies")
            if not dry_run:
                core.verify_dependencies()
            rprint(
                " :white_heavy_check_mark:[green] Dependencies installed and working"
            )

            # STEP 2: Manage code repos and local docker images
            rprint("[deep_sky_blue1]Pulling code and building local images")
            if not dry_run and not offline:
                pods = core.pull_and_build_pods()
            rprint(" :white_heavy_check_mark:[green] Pods built")

            # STEP 3: We need our own container registry
            rprint("[deep_sky_blue1]Container Registry")
            if not dry_run:
                core.start_registry()
            rprint(" :white_heavy_check_mark:[green] Registry Ready")
            progress.update(task, advance=5)

            # STEP 4: Populate the container registry with important images
            rprint("[deep_sky_blue1]Populating Container Registry for faster loading")
            if not dry_run:
                core.populate_registry()
            rprint(" :white_heavy_check_mark:[green] Registry Populated")
            progress.update(task, advance=5)

            # STEP 5: Start the k3s cluster
            rprint("[deep_sky_blue1]Cluster")
            if not dry_run:
                new_cluster = core.start_cluster(progress, task)
            rprint(" :white_heavy_check_mark:[green] Cluster Ready")
            progress.update(task, advance=33)

            # STEP 6: Load system containers
            rprint("[deep_sky_blue1]Loading system pods...")
            if not dry_run:
                # Load system containers and system containers requested by pods
                core.install_system_pods()

                if new_cluster:
                    core.create_databases()
            rprint(" :white_heavy_check_mark:[green] System Pods Loaded")
            progress.update(task, advance=33)

            # STEP 7: Build and load our pods
            rprint("[deep_sky_blue1]Building and loading pods...")
            if not dry_run:
                core.install_pods_in_cluster()
            rprint(" :white_heavy_check_mark:[green] Pods Loaded")
            progress.update(task, advance=34)

            # Let's give them a hint on how they can get started
            print()
            rprint("[italic]Hint: Some items may still be starting in k3s.")
            rprint("[italic]You can access your pod(s) via the following URLs:")
            for repo in pods:
                pod_name = repo["repo"].split("/")[-1:][0].replace(".git", "")
                if utils.check_host_entry(pod_name):
                    rprint(f"[italic]  http://{pod_name}.local:8088/")
    else:
        rprint(f"[steel_blue]Starting[/] {pod}")
        core.start_pod(pod)


@auto.command()
@click.pass_context
@click.argument("pod", required=False)
@click.option("--dry-run", is_flag=True, default=False)
@click.option("--delete-cluster", is_flag=True, default=False)
def stop(self, pod, dry_run, delete_cluster):  # pylint: disable=unused-argument
    """Stop the cluster (or delete it)"""

    if pod:
        # Stopping a pod
        rprint(f"[steel_blue]Stopping the [/]{pod}[steel_blue] pod")
        core.stop_pod(pod)

    else:
        # Stopping the entire cluster
        with Progress(transient=False) as progress:
            task = progress.add_task("Cluster Shutdown", total=100)

            if not dry_run:
                if delete_cluster:
                    core.delete_cluster(progress, task)
                else:
                    core.stop_cluster(progress, task)
            else:
                progress.update(task, advance=50)
            progress.update(task, advance=50)


@auto.command()
@click.pass_context
@click.argument("pod", required=True)
def restart(self, pod):  # pylint: disable=unused-argument
    """Restart (stop / start) a pod"""

    # Let's do this
    rprint(f"[steel_blue]Restarting [/]{pod}[steel_blue] pod")
    core.restart_pod(pod)


@auto.command()
@click.pass_context
@click.argument("pod", required=True)
def seed(self, pod):  # pylint: disable=unused-argument
    """Seed a pod's databases"""

    # We have to init the pod before we can seed it
    rprint(f"[steel_blue]Initializing [/]{pod}[steel_blue] pod")
    core.init_pod_db(pod)
    rprint()

    # Now we can seed the pod
    rprint(f"[steel_blue]Seeding [/]{pod}[steel_blue] pod")
    core.seed_pod(pod)


@auto.command()
@click.pass_context
@click.argument("pod", required=True)
def init(self, pod):  # pylint: disable=unused-argument
    """Init a pod's databases.

    `auto` can run a script to initialize your database inside the pod

    It will look for the the `db-init` config entry and then run that.
    if it doesn't see an entry it will let you know.
    """

    # Let's do this
    rprint(f"[steel_blue]Initializing [/]{pod}[steel_blue] pod database")
    core.init_pod_db(pod)


@auto.command()
@click.pass_context
def mysql(self):  # pylint: disable=unused-argument
    """Connect to the mysql database"""

    # Let's connect to the MySQL database inside the k3s cluster
    core.connect_to_mysql()


@auto.command()
@click.argument("pod")
@click.pass_context
def logs(self, pod):  # pylint: disable=unused-argument
    """Output logs for a pod to the terminal"""

    # Output the kubectl logs for a pod
    core.output_logs(pod)


@auto.command()
@click.argument("pod")
@click.pass_context
def tag(self, pod):  # pylint: disable=unused-argument
    """Build, Tag, and Load a pod container image in the local repository"""

    # Build, Tag, and load pod in local repository
    core.tag_pod_docker_image(pod)


@auto.command()
@click.argument("pod")
@click.pass_context
def upgrade(self, pod):  # pylint: disable=unused-argument
    """Remove container registry, create it again, then repopulate it, then restart the cluster"""

    # Build, Tag, and load pod in local repository
    core.tag_pod_docker_image(pod)
