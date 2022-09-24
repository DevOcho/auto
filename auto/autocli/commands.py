"""Auto Commands"""
import click
from autocli import core
from rich import print as rprint
from rich.progress import Progress

# Global settings for click
CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"], ignore_unknown_options=True)


@click.group(context_settings=CONTEXT_SETTINGS)
@click.version_option(version="0.0.1")
def greet():
    """Commandline utility to assist with creating/deleting clusters and
    starting/stopping pods."""


@greet.command()
@click.pass_context
@click.argument("pod", required=False)
@click.option("--dry-run", is_flag=True, default=False)
def start(self, pod, dry_run):  # pylint: disable=unused-argument
    """Start a new k3s/k3d cluster"""

    # Local Vars
    new_cluster = False

    if not pod:
        # Let's run the steps with a progress bar
        with Progress(transient=False) as progress:
            task = progress.add_task("Creating Dev Environment", total=100)

            # Are we ready?
            # STEP 1: Verify Docker is running on the host
            core.check_docker()

            # We need our own container registry
            rprint("[deep_sky_blue1]Container Registry")
            if not dry_run:
                core.start_registry(progress, task)
            rprint("       [green]Registry Ready")

            # Populate the container registry with important images
            rprint("[deep_sky_blue1]Populating Container Registry for faster loading")
            if not dry_run:
                core.populate_registry(progress, task)
            rprint("       [green]Registry Populated")

            # Start the k3s cluster
            rprint("[deep_sky_blue1]Cluster")
            if not dry_run:
                new_cluster = core.start_cluster(progress, task)
            rprint("       [green]Cluster Ready")
            progress.update(task, advance=33)

            # Load system containers
            rprint("[deep_sky_blue1]Loading system containers...")
            if not dry_run:
                core.install_mysql_in_cluster()
                if new_cluster:
                    core.create_databases()
            progress.update(task, advance=33)

            # Build and load our pods
            rprint("[deep_sky_blue1]Building and loading pods...")
            if not dry_run:
                core.install_pods_in_cluster()
            progress.update(task, advance=34)

            # Let's give them a hint on how they can get started
            print()
            rprint("[italic]Hint: Some items may still be starting in k3s.")
            rprint(
                "[italic]You can access the training pod via: http://training-pod.local:8088/"
            )
    else:
        rprint(f"[steel_blue]Starting[/] {pod}")
        core.start_pod(pod)


@greet.command()
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
            task = progress.add_task("Stopping Things", total=100)

            if not dry_run:
                if delete_cluster:
                    core.delete_cluster(progress, task)
                else:
                    core.stop_cluster(progress, task)
            else:
                progress.update(task, advance=50)
            progress.update(task, advance=50)


@greet.command()
@click.pass_context
@click.argument("pod", required=True)
def restart(self, pod):  # pylint: disable=unused-argument
    """Restart (stop / start) a pod"""

    # Let's do this
    rprint(f"[steel_blue]Restarting [/]{pod}[steel_blue] pod")
    core.restart_pod(pod)


@greet.command()
@click.pass_context
@click.argument("pod", required=True)
def seed(self, pod):  # pylint: disable=unused-argument
    """Seed a pod's databases"""

    # Let's do this
    rprint(f"[steel_blue]Seeding [/]{pod}[steel_blue] pod")
    core.seed_pod(pod)


@greet.command()
@click.pass_context
@click.argument("pod", required=True)
def initdb(self, pod):  # pylint: disable=unused-argument
    """Init a pod's databases"""

    # Let's do this
    rprint(f"[steel_blue]Initializing [/]{pod}[steel_blue] pod database")
    core.init_pod_db(pod)


@greet.command()
@click.pass_context
def mysql(self):  # pylint: disable=unused-argument
    """Connect to the mysql database"""

    # Let's connect to the MySQL database inside the k3s cluster
    core.connect_to_mysql()


@greet.command()
@click.argument("pod")
@click.pass_context
def logs(self, pod):  # pylint: disable=unused-argument
    """Output logs for a pod to the terminal"""

    # Output the kubectl logs for a pod
    core.output_logs(pod)


@greet.command()
@click.argument("pod")
@click.argument("version")
@click.pass_context
def tag(self, pod, version):  # pylint: disable=unused-argument
    """tag a new image"""

    # Let's connect to the MySQL database inside the k3s cluster
    core.tag_pod_docker_image(pod, version)
