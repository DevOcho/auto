"""Dependency and host preflight checks.

Each check_* returns a count of fatal problems (0 when satisfied) and prints
guidance via declare_error so verify_dependencies() can sum them and decide
whether to abort. Split out of utils.py to keep startup preflight logic in one
place.
"""

import json
import os
import shutil
import socket

from autocli.utils import declare_error, run_and_wait
from rich import print as rprint


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
    """Check for helm — an *optional* dependency.

    Pods deploy via either helm charts or raw kubectl manifests, so helm is
    only needed by pods that use a chart. A missing helm is therefore a
    warning, not a fatal error: we let the cluster come up and let the
    individual helm-based pod install fail loudly later if helm is genuinely
    required. Always returns 0 so it never blocks startup.
    """

    # check for the helm command
    bash_command = """helm version"""
    if not run_and_wait(bash_command, check_result="clean", suppress_error=True):
        rprint(
            "  [yellow]-- Note: `helm` was not found. This is fine unless a pod "
            "deploys via a helm chart.[/yellow]\n"
            "  [yellow]   Install it from https://helm.sh/docs/intro/install/ "
            "if you plan to use helm charts.[/yellow]"
        )

    # Helm is optional, so its absence never counts as a dependency error.
    return 0


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


def check_docker_insecure_registry():
    """Warn if Docker looks unable to push to the k3d registry over plain HTTP.

    Docker refuses HTTP pushes to registries it considers secure, but it treats
    anything resolving into 127.0.0.0/8 or ::1/128 as insecure automatically --
    and check_registry_host_entry() already requires k3d-registry.local in
    /etc/hosts. So the usual setup needs no daemon.json entry at all, and
    demanding one would block startup on a machine that pushes fine.

    That makes this advisory, like check_helm: it speaks up only when the
    registry host resolves somewhere other than loopback and no Docker config
    whitelists it, and it never adds to the fatal error count.
    """
    registry_host = "k3d-registry.local"
    registry_port = "12345"
    endpoint = f"{registry_host}:{registry_port}"

    # Loopback is insecure-by-default in Docker, so there is nothing to check
    try:
        resolved = socket.gethostbyname(registry_host)
        if resolved.startswith("127.") or resolved == "::1":
            return 0
    except OSError:
        # Name doesn't resolve at all -- check_registry_host_entry covers that
        return 0

    # Docker Desktop keeps its daemon config under the user's home directory,
    # the Linux daemon under /etc; check both before saying anything.
    daemon_configs = [
        "/etc/docker/daemon.json",
        os.path.expanduser("~/.docker/daemon.json"),
    ]
    for daemon_json in daemon_configs:
        try:
            if not os.path.isfile(daemon_json):
                continue
            with open(daemon_json, encoding="utf-8") as config_file:
                config = json.load(config_file)
            if endpoint in config.get("insecure-registries", []):
                return 0
        except (json.JSONDecodeError, OSError):
            continue

    rprint(
        f'  [yellow]-- Note: "{endpoint}" resolves to {resolved}, not loopback, '
        "and no Docker config lists it as an insecure registry.[/yellow]\n"
        "  [yellow]   Pushes to the local registry may be refused. Add this to "
        f"{daemon_configs[0]} and restart Docker if they are:[/yellow]\n"
        f'  [yellow]   {{"insecure-registries": ["{endpoint}"]}}[/yellow]'
    )
    return 0


def check_mkcert():
    """Check if mkcert is installed"""
    if not shutil.which("mkcert"):
        declare_error(
            "mkcert is not installed. Please install it to use HTTPS.\n"
            "    See: https://github.com/FiloSottile/mkcert"
            "Or set `HTTPS: false` in `~/.auto/config/local.yaml`"
        )
    # Also check for certutil so we don't fail partially
    check_certutil()
