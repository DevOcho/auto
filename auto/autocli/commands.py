"""Auto Commands

* `--dry-run`     This is for automated testing and visually testing the output
* `--offline`     This disables steps that require internet so you can work without Internet
"""

import os
import subprocess

import click
import requests
from autocli import core, platform, registry, services, utils
from autocli.config import CONFIG
from requests.exceptions import RequestException
from rich import print as rprint
from rich.progress import Progress

VERSION = "0.7.3"


# Global settings for click
CONTEXT_SETTINGS = {
    "help_option_names": ["-h", "--help"],
    "ignore_unknown_options": True,
}


def get_pod_names(ctx, param, incomplete):  # pylint: disable=unused-argument
    """Generate list of pods for shell autocompletion"""
    config_path = platform.auto_dir("config", "local.yaml")
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
            ["kubectl", "get", "ns", "-o", "jsonpath={.items[*].metadata.name}"]
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


# Subcommands that take a <pod> argument (used by the PowerShell completer to
# offer pod-name completion after them).
_POD_SUBCOMMANDS = "start stop restart logs seed init migrate rollback tag upgrade"

# Self-contained PowerShell completer. click has no native PowerShell completion,
# so this Register-ArgumentCompleter completes subcommands at the first position
# and pod names (read from local.yaml) for the subcommands that take a <pod>.
# __CMDS__/__PODCMDS__ are filled in by _powershell_completer().
_PS_COMPLETER_TEMPLATE = r"""Register-ArgumentCompleter -Native -CommandName auto -ScriptBlock {
    param($wordToComplete, $commandAst, $cursorPosition)
    $cmds = '__CMDS__'.Split(' ')
    $podCmds = '__PODCMDS__'.Split(' ')
    $typed = @($commandAst.CommandElements | Select-Object -Skip 1 | ForEach-Object { $_.Extent.Text })
    $onSub = ($typed.Count -eq 0) -or ($typed.Count -eq 1 -and $typed[0] -eq $wordToComplete)
    $results = @()
    if ($onSub) {
        $results = $cmds | Where-Object { $_ -like "$wordToComplete*" }
    } elseif ($podCmds -contains $typed[0]) {
        $cfg = Join-Path $env:USERPROFILE '.auto\config\local.yaml'
        if (Test-Path $cfg) {
            $results = (Get-Content $cfg) | Select-String 'repo:\s*(\S+)' | ForEach-Object {
                ($_.Matches[0].Groups[1].Value -replace '\.git$', '').Split('/')[-1]
            } | Where-Object { $_ -like "$wordToComplete*" }
        }
    }
    $results | ForEach-Object {
        [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_)
    }
}"""


def _powershell_completer():
    """Return the PowerShell argument completer for `auto`, with commands filled in."""
    cmds = " ".join(sorted(auto.commands.keys()))
    return _PS_COMPLETER_TEMPLATE.replace("__CMDS__", cmds).replace(
        "__PODCMDS__", _POD_SUBCOMMANDS
    )


_POLICY_FIX_CMD = "Set-ExecutionPolicy -Scope CurrentUser RemoteSigned"


def _ensure_powershell_policy():
    """Make sure the execution policy lets $PROFILE load; offer to fix it if not.

    On Windows the default policy is 'Restricted', which silently skips $PROFILE
    -- so the completer never registers and Tab just beeps. We ask the user
    (approval only when actually needed) and, if they agree, set the CurrentUser
    policy to RemoteSigned (no admin required, reversible).
    """
    policy = utils.run_and_return(
        ["powershell", "-NoProfile", "-Command", "Get-ExecutionPolicy"]
    ).strip()
    if policy.lower() not in ("restricted", "allsigned"):
        return  # already allows local profile scripts

    rprint(
        f"\n[yellow]Your PowerShell execution policy is [bright_cyan]{policy}[/], "
        "which blocks profile scripts -- so tab completion won't load.[/]"
    )
    if not click.confirm(f"Allow local scripts now? ({_POLICY_FIX_CMD})", default=True):
        rprint(f"Skipped. Enable it later with:\n  [bright_cyan]{_POLICY_FIX_CMD}[/]")
        return

    utils.run_and_wait(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Set-ExecutionPolicy -Scope CurrentUser RemoteSigned -Force",
        ],
        suppress_error=True,
    )
    # Re-check: a group policy could still enforce a stricter setting.
    new_policy = utils.run_and_return(
        ["powershell", "-NoProfile", "-Command", "Get-ExecutionPolicy"]
    ).strip()
    if new_policy.lower() not in ("restricted", "allsigned"):
        rprint(
            f":white_heavy_check_mark: [green]Execution policy is now "
            f"{new_policy}.[/] Revert anytime with: "
            "[bright_cyan]Set-ExecutionPolicy -Scope CurrentUser Undefined[/]"
        )
    else:
        rprint(
            "[red]Could not change it[/] (a group policy may enforce it). "
            f"Try manually:\n  [bright_cyan]{_POLICY_FIX_CMD}[/]"
        )


def _install_completion(shell, config_file, eval_line, reload_hint):
    """Install the completion snippet into the shell profile (idempotent)."""
    if shell == "powershell":
        # Resolve the real $PROFILE path from PowerShell itself.
        target = utils.run_and_return(
            ["powershell", "-NoProfile", "-Command", "$PROFILE"]
        ) or os.path.expanduser(
            "~/Documents/PowerShell/Microsoft.PowerShell_profile.ps1"
        )
    else:
        target = os.path.expanduser(config_file)

    marker = "# Autocomplete for auto CLI"
    already = False
    if os.path.isfile(target):
        with open(target, encoding="utf-8") as handle:
            already = marker in handle.read()

    if already:
        click.echo(f"Autocomplete is already installed in {target}.")
    else:
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(f"\n{marker}\n{eval_line}\n")
        rprint(
            f":white_heavy_check_mark: [green]Installed auto tab-completion in[/] {target}"
        )

    rprint(f'Open a NEW terminal (or run "{reload_hint}") to start using it.')
    if shell == "powershell":
        _ensure_powershell_policy()


@auto.command()
@click.option(
    "--shell", default=None, help="Shell type (bash, zsh, fish, or powershell)."
)
@click.option(
    "--install",
    "do_install",
    is_flag=True,
    help="Install it automatically into your shell profile.",
)
def autocomplete(shell, do_install):
    """Enable tab-completion for auto (use --install to set it up automatically)."""
    # Default to the native shell for the platform.
    if not shell:
        shell = "powershell" if platform.IS_WINDOWS else "bash"

    if shell == "bash":
        eval_line = 'eval "$(_AUTO_COMPLETE=bash_source auto)"'
        config_file = "~/.bashrc"
        reload_hint = f"source {config_file}"
    elif shell == "zsh":
        eval_line = 'eval "$(_AUTO_COMPLETE=zsh_source auto)"'
        config_file = "~/.zshrc"
        reload_hint = f"source {config_file}"
    elif shell == "fish":
        eval_line = "eval (env _AUTO_COMPLETE=fish_source auto)"
        config_file = "~/.config/fish/config.fish"
        reload_hint = f"source {config_file}"
    elif shell == "powershell":
        eval_line = _powershell_completer()
        config_file = "$PROFILE"
        reload_hint = ". $PROFILE"
    else:
        raise click.BadOptionUsage("--shell", f"Unsupported shell: {shell}")

    if do_install:
        _install_completion(shell, config_file, eval_line, reload_hint)
        return

    # No --install: lead with the one-shot installer, then show the manual line.
    rprint(f"[bold]To enable {shell} tab-completion for [bright_cyan]auto[/]:[/]\n")
    rprint("  Run once:  [bright_cyan]auto autocomplete --install[/]")
    rprint("  Then open a new terminal.\n")
    rprint(f"[italic]Or add this to {config_file} manually:[/]")
    click.echo(eval_line)


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


def _latest_release_version():
    """Return the latest published version tag (without leading 'v'), or ''."""
    try:
        resp = requests.get(
            "https://api.github.com/repos/devocho/auto/releases/latest", timeout=30
        )
        resp.raise_for_status()
        return resp.json()["tag_name"].lstrip("v")
    except (RequestException, KeyError, ValueError):
        return ""


def _run_self_update():
    """Kick off the self-update for the current platform."""
    if platform.IS_WINDOWS:
        rprint("To update on Windows, run this in PowerShell:")
        rprint("  [bright_cyan]iwr -useb https://www.devocho.com/auto.ps1 | iex[/]")
    else:
        subprocess.run(
            ["bash", "-c", "curl -fsSL https://www.devocho.com/auto.sh | bash"],
            check=False,
        )


@auto.command()
@click.pass_context
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Force update even if already at the latest version.",
)
def update(self, force):  # pylint: disable=unused-argument
    """Update auto CLI to the latest version"""
    latest_version = _latest_release_version()
    if latest_version:
        try:
            # --force (from 0.7.3) updates even when already on the latest version.
            if VERSION == latest_version and not force:
                rprint(f"[green]Current version ({VERSION}) is already the latest.[/]")
                rprint("""
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
    """)
                return
            rprint(f"[steel_blue]Updating from {VERSION} to {latest_version}...[/]")
        except Exception:  # pylint: disable=broad-except
            pass

    _run_self_update()


@auto.command()
@click.option(
    "--fix",
    is_flag=True,
    default=False,
    help="Attempt to install missing prerequisites (Windows: winget).",
)
@click.pass_context
def doctor(self, fix):  # pylint: disable=unused-argument
    """Check (and optionally install) the tools auto needs."""
    utils.run_doctor(fix)
