"""Auto Commands

  * `--dry-run`     This is for automated testing and visually testing the output
  * `--offline`     This disables steps that require internet so you can work without Internet
"""

import os

import click
from autocli import core, utils
from rich import print as rprint
from rich.progress import Progress

# Global settings for click
CONTEXT_SETTINGS = {
    "help_option_names": ["-h", "--help"],
    "ignore_unknown_options": True,
}


def get_pod_names(ctx, param, incomplete):  # pylint: disable=unused-argument
    """Generate list of pods for shell autocompletion"""
    # Check for config file existence to avoid error printing/exit in utils.load_config
    config_path = os.path.expanduser("~/.auto/config/local.yaml")
    if not os.path.isfile(config_path):
        return []

    try:
        # We suppress output here to avoid breaking the shell completion display
        config = utils.load_config()
        pods = []
        for item in config.get("pods", []):
            # Logic to extract pod name from repo string
            # e.g. git@github.com:DevOcho/portal.git -> portal
            if isinstance(item, dict) and "repo" in item:
                p_name = item["repo"].split("/")[-1:][0].replace(".git", "")
                if p_name.startswith(incomplete):
                    pods.append(p_name)
        return sorted(pods)
    except Exception:  # pylint: disable=broad-except
        return []


def get_namespaces(ctx, param, incomplete):  # pylint: disable=unused-argument
    """Generate list of namespaces for shell autocompletion"""
    try:
        # Get namespaces from kubectl
        # We rely on utils.run_and_return which is safe and captures output
        output = utils.run_and_return(
            "kubectl get ns -o jsonpath='{.items[*].metadata.name}'"
        )
        if not output:
            return []

        namespaces = output.split()
        return [ns for ns in namespaces if ns.startswith(incomplete)]
    except Exception:  # pylint: disable=broad-except
        return []


@click.group(context_settings=CONTEXT_SETTINGS)
@click.version_option(version="0.3.0")
def auto():
    """Commandline utility to assist with creating/deleting clusters and
    starting/stopping pods.

    This function is used to group all of the commands together.
    """


@auto.command()
@click.option("--shell", default="bash", help="Shell type (bash, zsh, or fish).")
@click.option(
    "--install",
    "do_install",
    is_flag=True,
    help="Automatically append to shell config (use with caution).",
)
def autocomplete(shell, do_install):
    """Display instructions to enable shell autocomplete (or install it)."""
    if shell == "bash":
        eval_line = 'eval "$(_AUTO_COMPLETE=bash_source auto)"'
        config_file = "~/.bashrc"
    elif shell == "zsh":
        eval_line = 'eval "$(_AUTO_COMPLETE=zsh_source auto)"'
        config_file = "~/.zshrc"
    elif shell == "fish":
        eval_line = "eval (env _AUTO_COMPLETE=fish_source auto)"
        config_file = "~/.config/fish/config.fish"
    else:
        raise click.BadOptionUsage("--shell", f"Unsupported shell: {shell}")

    click.echo(
        f'To enable {shell} completion for "auto", add this line to {config_file}:'
    )
    click.echo(eval_line)
    click.echo(f'\nThen reload your shell (e.g., "source {config_file}").')
    click.echo('Once set up, tab after "auto" to see commands, options, etc.')

    if do_install:
        click.confirm(
            f"\nAppend to {config_file} now? (This modifies your file)", abort=True
        )
        # Note: os is imported at module level, no need to re-import
        with open(os.path.expanduser(config_file), "a", encoding="utf-8") as f:
            f.write(f"\n# Autocomplete for auto CLI\n{eval_line}\n")
        click.echo(f'Added to {config_file}. Run "source {config_file}" to activate.')


def _setup_https_certificates(pods):
    """Helper to setup HTTPS certificates interactively"""
    rprint("[deep_sky_blue1]Setting up HTTPS certificates...[/]")
    # Check dependencies first
    utils.check_mkcert()

    # Calculate specific domains for pods
    pod_domains = []
    for repo in pods:
        p_name = repo["repo"].split("/")[-1:][0].replace(".git", "")
        pod_domains.append(f"{p_name}.local")

    # Create certs interactively
    cert_path = os.path.expanduser("~") + "/.auto/certs"
    key_file, cert_file = utils.create_local_certs(
        cert_path, additional_domains=pod_domains
    )
    rprint(" :white_heavy_check_mark:[green] Certificates Ready")
    return key_file, cert_file


def _print_access_hints(pods, use_https):
    """Helper to print access hints at the end of start"""
    # Let's give them a hint on how they can get started
    print()
    rprint("[italic]Hint: Some items may still be starting in k3s.")
    rprint("[italic]You can access your pod(s) via the following URLs:")

    # Determine protocol and port based on config
    protocol = "https" if use_https else "http"
    port_suffix = "" if use_https else ":8088"

    for repo in pods:
        pod_name = repo["repo"].split("/")[-1:][0].replace(".git", "")
        if utils.check_host_entry(pod_name):
            rprint(f"[italic]  {protocol}://{pod_name}.local{port_suffix}/")


@auto.command()
@click.pass_context
@click.argument("pod", required=False, shell_complete=get_pod_names)
@click.option("--dry-run", is_flag=True, default=False)
@click.option("--offline", is_flag=True, default=False)
def start(self, pod, dry_run, offline):  # pylint: disable=unused-argument
    """Start a new k3s/k3d cluster"""

    # Local Vars
    new_cluster = False
    pods = core.CONFIG.get("pods", [])
    key_file = ""
    cert_file = ""
    use_https = core.CONFIG.get("https", False)

    # HTTPS Cert Setup (interactive steps must happen before Progress bar)
    if not pod and use_https and not dry_run:
        key_file, cert_file = _setup_https_certificates(pods)

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
                new_cluster = core.start_cluster(
                    progress, task, key_file=key_file, cert_file=cert_file
                )
            rprint(" :white_heavy_check_mark:[green] Cluster Ready")
            progress.update(task, advance=33)

            # STEP 6: Load system containers
            rprint("[deep_sky_blue1]Loading system pods...")
            if not dry_run:
                # Load system containers
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

            _print_access_hints(pods, use_https)
    else:
        rprint(f"[steel_blue]Starting[/] {pod}")
        core.start_pod(pod)


@auto.command()
@click.pass_context
@click.argument("pod", required=False, shell_complete=get_pod_names)
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
@click.argument("pod", required=True, shell_complete=get_pod_names)
def restart(self, pod):  # pylint: disable=unused-argument
    """Restart (stop / start) a pod"""

    # Let's do this
    rprint(f"[steel_blue]Restarting [/]{pod}[steel_blue] pod")
    core.restart_pod(pod)


@auto.command()
@click.pass_context
@click.argument("pod", required=True, shell_complete=get_pod_names)
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
@click.argument("pod", required=True, shell_complete=get_pod_names)
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
@click.pass_context
def minio(self):  # pylint: disable=unused-argument
    """Open Connection to MinIO Server"""

    # Start a port-forward to the minio host
    core.connect_to_minio()


@auto.command()
@click.argument("pod", shell_complete=get_pod_names)
@click.pass_context
def logs(self, pod):  # pylint: disable=unused-argument
    """Output logs for a pod to the terminal"""

    # Output the kubectl logs for a pod
    core.output_logs(pod)


@auto.command()
@click.argument("pod", shell_complete=get_pod_names)
@click.pass_context
def tag(self, pod):  # pylint: disable=unused-argument
    """Build, Tag, and Load a pod container image in the local repository"""

    # Build, Tag, and load pod in local repository
    core.tag_pod_docker_image(pod)


@auto.command()
@click.argument("pod", shell_complete=get_pod_names)
@click.pass_context
def upgrade(self, pod):  # pylint: disable=unused-argument
    """Remove container registry, create it again, then repopulate it, then restart the cluster"""

    # Build, Tag, and load pod in local repository
    core.tag_pod_docker_image(pod)


@auto.command()
@click.argument("pod", shell_complete=get_pod_names)
@click.pass_context
def migrate(self, pod):  # pylint: disable=unused-argument
    """Run database migrations in a pod (using smalls)"""

    # Build, Tag, and load pod in local repository
    core.migrate_with_smalls(pod)


@auto.command()
@click.argument("pod", shell_complete=get_pod_names)
@click.argument("number")
@click.pass_context
def rollback(self, pod, number):  # pylint: disable=unused-argument
    """Rollback database migrations in a pod (using smalls)"""

    # Build, Tag, and load pod in local repository
    core.rollback_with_smalls(pod, number)


@auto.command()
@click.pass_context
@click.argument("git_repo", required=True)
def install(self, git_repo):  # pylint: disable=unused-argument
    """Install "parent" configuration file from git repo"""

    # This will download a repo and then copy the local.yaml file
    # into the auto config folder
    core.install_config_from_repo(git_repo)


@auto.command()
@click.pass_context
@click.option(
    "--namespace",
    "-n",
    default="default",
    help="Namespace to show pods for",
    shell_complete=get_namespaces,
)
@click.option(
    "--all-namespaces",
    "-a",
    is_flag=True,
    default=False,
    help="Show pods from all namespaces",
)
@click.option(
    "--watch",
    "-w",
    is_flag=True,
    default=False,
    help="Watch the status (refresh every 3s)",
)
def status(self, namespace, all_namespaces, watch):  # pylint: disable=unused-argument
    """Show the status of the cluster and pods"""

    core.show_status(namespace, all_namespaces, watch)
