"""Auto Commands

  * `--dry-run`     This is for automated testing and visually testing the output
  * `--offline`     This disables steps that require internet so you can work without Internet
"""

import json
import os
import re
import subprocess

import click
from autocli import core, registry, services, utils
from autocli.config import CONFIG
from rich import print as rprint
from rich.progress import Progress

VERSION = "0.7.6"


# Global settings for click
CONTEXT_SETTINGS = {
    "help_option_names": ["-h", "--help"],
    "ignore_unknown_options": True,
}


def get_pod_names(ctx, param, incomplete):  # pylint: disable=unused-argument
    """Generate list of pods for shell autocompletion"""
    config_path = os.path.expanduser("~/.auto/config/local.yaml")
    if not os.path.isfile(config_path):
        return []

    try:
        pods = []
        for item in CONFIG.get("pods", []):
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
@click.version_option(version=VERSION)
def auto():
    """Commandline utility to assist with creating/deleting clusters and
    starting/stopping pods."""
    return


@auto.command(name="images")
@click.pass_context
def images(self):  # pylint: disable=unused-argument
    """List unique container images running in the cluster (formatted for local.yaml)."""
    registry.list_cluster_images()


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

    if do_install:
        click.confirm(
            f"\nAppend to {config_file} now? (This modifies your file)", abort=True
        )
        with open(os.path.expanduser(config_file), "a", encoding="utf-8") as f:
            f.write(f"\n# Autocomplete for auto CLI\n{eval_line}\n")
        click.echo(f'Added to {config_file}. Run "source {config_file}" to activate.')


@auto.command()
@click.pass_context
@click.argument("pod", required=False, shell_complete=get_pod_names)
@click.option("--dry-run", is_flag=True, default=False)
@click.option("--offline", is_flag=True, default=False)
def start(self, pod, dry_run, offline):  # pylint: disable=unused-argument
    """Start a new k3s/k3d cluster or an individual pod"""
    core.bootstrap_cluster(pod, dry_run, offline)


@auto.command()
@click.pass_context
@click.argument("pod", required=False, shell_complete=get_pod_names)
@click.option("--dry-run", is_flag=True, default=False)
@click.option("--delete-cluster", is_flag=True, default=False)
def stop(self, pod, dry_run, delete_cluster):  # pylint: disable=unused-argument
    """Stop the cluster (or delete it)"""
    if pod:
        rprint(f"[steel_blue]Stopping the [/]{pod}[steel_blue] pod")
        core.stop_pod(pod)
    else:
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
    rprint(f"[steel_blue]Restarting [/]{pod}[steel_blue] pod")
    core.restart_pod(pod)


@auto.command()
@click.pass_context
@click.argument("pod", required=True, shell_complete=get_pod_names)
def seed(self, pod):  # pylint: disable=unused-argument
    """Seed a pod's databases"""
    rprint(f"[steel_blue]Initializing[/] {pod}[steel_blue] pod")
    services.init_pod_db(pod)
    rprint()
    rprint(f"[steel_blue]Seeding [/]{pod}[steel_blue] pod")
    services.seed_pod(pod)


@auto.command()
@click.pass_context
@click.argument("pod", required=True, shell_complete=get_pod_names)
def init(self, pod):  # pylint: disable=unused-argument
    """Init a pod's databases"""
    rprint(f"[steel_blue]Initializing [/]{pod}[steel_blue] pod database")
    services.init_pod_db(pod)


@auto.command()
@click.pass_context
def mysql(self):  # pylint: disable=unused-argument
    """Connect to the mysql database"""
    services.connect_to_mysql()


@auto.command()
@click.pass_context
def postgres(self):  # pylint: disable=unused-argument
    """Connect to the postgres database"""
    services.connect_to_postgres()


@auto.command()
@click.pass_context
def minio(self):  # pylint: disable=unused-argument
    """Open Connection to MinIO Server"""
    services.connect_to_minio()


@auto.command()
@click.pass_context
def mailpit(self):  # pylint: disable=unused-argument
    """Open the Mailpit inbox (email sent by your pods)"""
    services.connect_to_mailpit()


@auto.command()
@click.argument("pod", shell_complete=get_pod_names)
@click.pass_context
def logs(self, pod):  # pylint: disable=unused-argument
    """Output logs for a pod to the terminal"""
    core.output_logs(pod)


@auto.command()
@click.argument("pod", shell_complete=get_pod_names)
@click.pass_context
def tag(self, pod):  # pylint: disable=unused-argument
    """Build, Tag, and Load a pod container image in the local repository"""
    registry.tag_pod_docker_image(pod)


@auto.command()
@click.argument("pod", shell_complete=get_pod_names)
@click.pass_context
def upgrade(self, pod):  # pylint: disable=unused-argument
    """Remove container registry, create it again, then repopulate it, then restart the cluster"""
    registry.tag_pod_docker_image(pod)


@auto.command()
@click.argument("pod", shell_complete=get_pod_names)
@click.pass_context
def migrate(self, pod):  # pylint: disable=unused-argument
    """Run database migrations in a pod (using smalls)"""
    core.migrate_with_smalls(pod)


@auto.command()
@click.argument("pod", shell_complete=get_pod_names)
@click.argument("number")
@click.pass_context
def rollback(self, pod, number):  # pylint: disable=unused-argument
    """Rollback database migrations in a pod (using smalls)"""
    core.rollback_with_smalls(pod, number)


@auto.command()
@click.pass_context
@click.argument("git_repo", required=True)
def install(self, git_repo):  # pylint: disable=unused-argument
    """Install "parent" configuration file from git repo"""
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


_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
INSTALLER = "curl -fsSL https://www.devocho.com/auto.sh | bash"


def _normalize_version(raw):
    """Strip a single leading 'v' and return the X.Y.Z version (None if invalid)"""
    candidate = raw[1:] if raw.startswith("v") else raw
    return candidate if _VERSION_RE.match(candidate) else None


def _release_http_status(version):
    """Return the HTTP status of the GitHub release-tag lookup ('' if curl failed)"""
    result = subprocess.run(
        [
            "curl",
            "-s",
            "-o",
            os.devnull,
            "-w",
            "%{http_code}",
            f"https://api.github.com/repos/devocho/auto/releases/tags/v{version}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip()


def _describe_direction(target):
    """Human phrase for moving from the running VERSION to the target one"""
    try:
        current = tuple(int(part) for part in VERSION.split("."))
        wanted = tuple(int(part) for part in target.split("."))
    except ValueError:
        return "Switching to"
    if wanted < current:
        return "Rolling back"
    if wanted > current:
        return "Updating"
    return "Reinstalling"


def _update_to_version(version, force, dry_run):
    """Pin auto to a specific release (works as an upgrade or a rollback)"""
    if force:
        rprint(
            "[red]--force has no effect with an explicit VERSION[/] "
            "(it only applies when updating to latest)."
        )
        raise SystemExit(1)

    target = _normalize_version(version)
    if target is None:
        rprint(f"[red]Invalid version:[/] '{version}'. Expected X.Y.Z (e.g. 0.7.1).")
        raise SystemExit(1)

    # Confirm the release exists before touching the installed binary
    http_status = _release_http_status(target)
    if http_status == "404":
        rprint(f"[red]auto v{target} not found[/] (no such release).")
        raise SystemExit(1)
    if http_status in ("403", "429"):
        rprint(
            f"[red]GitHub API rate limit reached[/] (HTTP {http_status}); try again shortly."
        )
        raise SystemExit(1)
    if http_status != "200":
        detail = http_status or "no response"
        rprint(
            f"[red]Could not reach GitHub to verify v{target}[/] ({detail}); check your connection."
        )
        raise SystemExit(1)

    rprint(f"[steel_blue]{_describe_direction(target)} {VERSION} -> {target}...[/]")

    if dry_run:
        rprint(f"[grey58]Dry run:[/] would run AUTO_VERSION={target} {INSTALLER}")
        return

    # The version travels via the environment so it never touches a shell string
    subprocess.run(
        ["bash", "-c", INSTALLER],
        env={**os.environ, "AUTO_VERSION": target},
        check=False,
    )


@auto.command()
@click.pass_context
@click.argument("version", required=False)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Force update even if already at the latest version (ignored with VERSION).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show what would happen without changing anything.",
)
def update(self, version, force, dry_run):  # pylint: disable=unused-argument
    """Update auto to the latest release, or roll back/forward to a specific VERSION (e.g. 0.7.1)"""
    if version is not None:
        _update_to_version(version, force, dry_run)
        return

    latest_version_json = utils.run_and_return(
        "curl -s https://api.github.com/repos/devocho/auto/releases/latest"
    )
    if latest_version_json:
        try:
            latest_version_data = json.loads(latest_version_json)
            latest_version = latest_version_data["tag_name"].lstrip("v")
            if VERSION == latest_version and not force:
                rprint(f"[green]Current version ({VERSION}) is already the latest.[/]")
                rprint(
                    """
⠀⠀⠀⠀⠀⠀⠀⠀⣠⣴⣶⡋⠉⠙⠒⢤⡀⠀⠀⠀⠀⠀⢠⠖⠉⠉⠙⠢⡄⠀
⠀⠀⠀⠀⠀⠀⢀⣼⣟⡒⠒⠀⠀⠀⠀⠀⠙⣆⠀⠀⠀⢠⠃⠀⠀⠀⠀⠀⠹⡄
⠀⠀⠀⠀⠀⠀⣼⠷⠖⠀⠀⠀⠀⠀⠀⠀⠀⠘⡆⠀⠀⡇⠀⠀⠀⠀⠀⠀⠀⢷
⠀⠀⠀⠀⠀⠀⣷⡒⠀⠀⢐⣒⣒⡒⠀⣐⣒⣒⣧⠀ ⡇⠀⠀⢠⢤⢠⡠⠀⢸⠀
⠀⠀⠀⠀⠀⢰⣛⣟⣂⠀⠘⠤⠬⠃⠰⠑⠥⠊⣿⠀ ⡇⠀⠀⠓⠃⠋⠂⠀⢸⠀
⠀⠀⠀⠀⠀⢸⣿⡿⠤⠀⢸⠁⠀⠀⢀⡆⠀⠀⣿⠀⠀⡇⠀⠀⠀⠀⠀⠀⠀⣸
⠀⠀⠀⠀⠀⠈⠿⣯⡭⠀⠸⡀⠀⢀⣀⠀⠀⠀⡟⠀⠀⢸⠀⠀⠀⠀⠀⠀⢠⠏
⠀⠀⠀⠀⠀⠀⠀⠈⢯⡥⠄⢱⠀⠀⠀⠀⠀⡼⠁⠀⠀⠀⠳⢄⣀⣀⣀⡴⠃⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⢱⡦⣄⣀⣀⣀⣠⠞⠁⠀⠀⠀⠀⠀⠀⠈⠉⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⢀⣤⣾⠛⠃⠀⠀⠀⢹⠳⡶⣤⡤⣄⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⣠⢴⣿⣿⣿⡟⡷⢄⣀⣀⣀⡼⠳⡹⣿⣷⠞⣳⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⢰⡯⠭⠹⡟⠿⠧⠷⣄⣀⣟⠛⣦⠔⠋⠛⠛⠋⠙⡆⠀⠀⠀⠀⠀⠀⠀
⠀⠀⢸⣿⠭⠉⠀⢠⣤⠀⠀⠀⠘⡷⣵⢻⠀⠀⠀⠀⣼⠀⣇⠀⠀⠀⠀⠀⠀⠀
⠀⠀⡇⣿⠍⠁⠀⢸⣗⠂⠀⠀⠀⣧⣿⣼⠀⠀⠀⠀⣯⠀⢸⠀⠀⠀⠀⠀⠀⠀
    """
                )
                return
            rprint(f"[steel_blue]Updating from {VERSION} to {latest_version}...[/]")
        except Exception:  # pylint: disable=broad-except
            pass
    if dry_run:
        rprint(f"[grey58]Dry run:[/] would run {INSTALLER}")
        return
    os.system(INSTALLER)
