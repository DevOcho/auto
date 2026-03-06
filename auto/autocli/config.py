"""Configuration logic for the auto CLI"""

import os
import sys

import yaml
from rich import print as rprint
from rich.prompt import Confirm


def _fatal_error(msg, exit_code=1):
    """Print a standard error message and exit to prevent cyclic imports with utils"""
    rprint(f"\n[red]:x: Error[/red]: {msg}")
    sys.exit(exit_code)


def load_config():
    """Load the global auto config"""
    config = {}
    config_path = os.path.expanduser("~") + "/.auto/config/local.yaml"

    if not os.path.isfile(config_path):
        rprint(
            "\n [yellow]:warning: No local.yaml file available. Creating a default.[/yellow]"
        )
        create_initial_config()

    with open(config_path, encoding="utf-8") as yaml_file:
        config = yaml.safe_load(yaml_file)

    if "code" in config:
        expanded_path = os.path.expanduser(config["code"])
        expanded_path = os.path.expandvars(expanded_path)
        config["code"] = expanded_path

        if not os.path.exists(config["code"]):
            rprint(
                f"\n[yellow]Warning: Code folder '{config['code']}' does not exist.[/]"
            )
            if Confirm.ask("Do you want us to create it for you?"):
                try:
                    os.makedirs(config["code"])
                    rprint(f"[green]Created directory: {config['code']}[/]")
                except OSError as e:
                    _fatal_error(f"Could not create directory: {e}")
            else:
                _fatal_error("Code directory missing. Cannot proceed.")

    return config


def create_initial_config():
    """Create a default config file if none is present"""
    default_config = """
---
# The code folder is where you want us to download all of your pod code repositories
code: ${HOME}/source/devocho

# HTTPS in local
# Set to `false` if you don't want this.  If it's false we will use port 8088 for local pod access.
https: true

# Each repo listed here will be run as a pod in k3s
pods:
  - repo: https://github.com/DevOcho/portal.git
    branch: main

# These are the system pods.  They use the config that comes with auto.
system-pods:
  - pod:
      name: mysql
      active: false
      commands:[
          "kubectl apply -f ~/.auto/k3s/mysql/pv.yaml",
          "kubectl apply -f ~/.auto/k3s/mysql/pvc.yaml",
          "kubectl apply -f ~/.auto/k3s/mysql/deployment.yaml",
          "kubectl apply -f ~/.auto/k3s/mysql/service.yaml",
          "kubectl apply -f ~/.auto/k3s/mysql/ingress.yaml",
        ]
      databases:
        - name: portal
  - pod:
      name: postgres
      active: false
      commands:[
          "kubectl apply -f ~/.auto/k3s/postgres/configmap.yaml",
          "kubectl apply -f ~/.auto/k3s/postgres/pv.yaml",
          "kubectl apply -f ~/.auto/k3s/postgres/pvc.yaml",
          "kubectl apply -f ~/.auto/k3s/postgres/deployment.yaml",
          "kubectl apply -f ~/.auto/k3s/postgres/service.yaml",
          "kubectl apply -f ~/.auto/k3s/postgres/ingress.yaml",
        ]
      databases:
        - name: portal
  - pod:
      name: minio
      active: false
      commands:[
          "kubectl apply -f ~/.auto/k3s/minio/pv.yaml",
          "kubectl apply -f ~/.auto/k3s/minio/pvc.yaml",
          "kubectl apply -f ~/.auto/k3s/minio/deployment.yaml",
          "kubectl apply -f ~/.auto/k3s/minio/service.yaml",
          "kubectl apply -f ~/.auto/k3s/minio/ingress.yaml",
        ]
      databases:
        - name: portal
"""
    config_dir = os.path.expanduser("~") + "/.auto/config"
    config_file = config_dir + "/local.yaml"
    if not os.path.isfile(config_file):
        os.makedirs(config_dir, exist_ok=True)
        with open(config_file, "w", encoding="utf-8") as f:
            f.write(default_config)


def _get_registry_bounds(lines):
    """Find the start and end indices of the registry block in the YAML file."""
    registry_idx = -1
    for i, line in enumerate(lines):
        if line.startswith("registry:"):
            registry_idx = i
            break

    if registry_idx == -1:
        return -1, -1

    last_valid_idx = registry_idx + 1
    insert_idx = registry_idx + 1

    while insert_idx < len(lines):
        line = lines[insert_idx]
        if (
            line.strip() != ""
            and not line.startswith(" ")
            and not line.startswith("-")
            and not line.startswith("#")
        ):
            break
        if line.strip().startswith("- image:"):
            last_valid_idx = insert_idx + 1
        insert_idx += 1

    # Move past immediate trailing comments gracefully
    if last_valid_idx == registry_idx + 1:
        while last_valid_idx < len(lines) and lines[last_valid_idx].strip().startswith(
            "#"
        ):
            last_valid_idx += 1

    return registry_idx, last_valid_idx


def _update_config_memory(new_images):
    """Update the in-memory CONFIG so it's globally accurate immediately."""
    if "registry" not in CONFIG or CONFIG["registry"] is None:
        CONFIG["registry"] = []

    for img in new_images:
        if not any(existing.get("image") == img for existing in CONFIG["registry"]):
            CONFIG["registry"].append({"image": img})


def add_images_to_local_config(new_images):
    """Safely append new images to the registry list in local.yaml without deleting comments."""
    if not new_images:
        return

    config_path = os.path.expanduser("~/.auto/config/local.yaml")
    if not os.path.isfile(config_path):
        return

    with open(config_path, encoding="utf-8") as f:
        lines = f.readlines()

    registry_idx, last_valid_idx = _get_registry_bounds(lines)

    if registry_idx != -1:
        new_lines = []
        existing_registry_lines = lines[registry_idx:last_valid_idx]
        for img in new_images:
            if not any(img in ln for ln in existing_registry_lines):
                new_lines.append(f"  - image: {img}\n")

        lines = lines[:last_valid_idx] + new_lines + lines[last_valid_idx:]
    else:
        lines.append("\nregistry:\n")
        for img in new_images:
            lines.append(f"  - image: {img}\n")

    with open(config_path, "w", encoding="utf-8") as f:
        f.writelines(lines)

    _update_config_memory(new_images)


# Populate global configuration exactly once on load
CONFIG = load_config()
