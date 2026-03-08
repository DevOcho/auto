"""System Pods and Database Services Management"""

import time

from autocli import utils
from autocli.config import CONFIG
from rich import print as rprint


def _run_command_with_retry(command):
    """Helper to run a command with retries"""
    for _ in range(10):
        try:
            # Attempt to apply with suppressed errors for cleaner startup logs
            success = utils.run_and_wait(
                command, capture_output=True, suppress_error=True
            )
            if success:
                break
            time.sleep(2)
        except Exception:  # pylint: disable=broad-except
            pass
    else:
        # If we exhausted retries, try one last time WITH errors to show user
        if not utils.run_and_wait(command):
            rprint(f"    [red]Error running {command}")


def _expose_system_pod_port(pod_name, mapping):
    """Helper to expose ports for requested system pods dynamically"""
    host_port = mapping["host"]
    lb_port = mapping["lb"]
    desc = mapping["desc"]

    # Is port already exposed on k3d loadbalancer?
    if utils.is_port_exposed_on_k3d(host_port):
        return True

    # Check if port is already running locally to prevent collision
    if utils.is_port_in_use(host_port):
        rprint(
            f"  [yellow]WARNING: Port {host_port} ({desc}) is already in use on\n"
            f" the system so we are not starting pod {pod_name}[/yellow]"
        )
        # Note that we skipped this pod so dependencies aren't built on failures
        CONFIG.setdefault("skipped-system-pods", []).append(pod_name)
        return False

    # Not in use locally or via k3d, let's inject and bind it dynamically
    rprint(f"  -- Exposing Port {host_port} for {desc}")
    utils.run_and_wait(
        f"k3d node edit k3d-k3s-default-serverlb --port-add {host_port}:{lb_port}"
    )
    return True


def install_system_pods():
    """Install all of the system pods in the cluster"""

    # We need to know which ones to start (both configured explicitly or requested implicitly)
    required_pods = utils.get_required_system_pods(CONFIG)

    port_mappings = {
        "mysql": {"host": 3306, "lb": 30036, "desc": "MySQL"},
        "postgres": {"host": 5432, "lb": 30035, "desc": "Postgres"},
        "mssql": {"host": 1433, "lb": 30034, "desc": "SQL Server"},
    }

    # Let's start the ones that we find that are "active" or requested
    for sys_pod in CONFIG.get("system-pods", []):
        pod_name = sys_pod["pod"]["name"]

        # Proceed only if this pod was identified as required
        if pod_name not in required_pods:
            continue

        # Check port mappings and expose dynamically if necessary
        if pod_name in port_mappings:
            if not _expose_system_pod_port(pod_name, port_mappings[pod_name]):
                # If the port exposure failed (due to local collision), we skip installing
                continue

        rprint("  -- Starting: " + pod_name)
        for command in sys_pod["pod"]["commands"]:
            _run_command_with_retry(command)

        # MinIO has some extra setup stuff needed to use it
        if pod_name == "minio":
            utils.setup_minio()


def _process_mysql_databases(system_pod):
    """Helper to process MySQL database creation"""
    for database in system_pod.get("databases", []):
        utils.create_mysql_database(database["name"])
        rprint(f"      *  Created MySQL database:[bright_cyan]{database['name']}")


def _process_minio_buckets(system_pod):
    """Helper to process MinIO bucket creation"""
    for bucket in system_pod.get("buckets", []):
        utils.create_minio_bucket(bucket["name"])
        rprint(f"      *  Created MinIO bucket:[bright_cyan]{bucket['name']}")


def _process_pod_databases(pod_config):
    """Helper to process database creation for a single pod config"""
    if "system-pods" not in pod_config:
        return

    skipped_pods = CONFIG.get("skipped-system-pods", [])

    for system_pod in pod_config["system-pods"]:
        if system_pod.get("name") in skipped_pods:
            continue

        if system_pod.get("name") == "mysql":
            _process_mysql_databases(system_pod)
        elif system_pod.get("name") == "minio":
            _process_minio_buckets(system_pod)


def create_databases():
    """Create the databases"""
    rprint("  -- Creating Databases and Buckets")

    # If the user port-blocked the MySQL launch earlier, let's fail gracefully here.
    if "mysql" in CONFIG.get("skipped-system-pods", []):
        rprint(
            "       [yellow]Skipping MySQL database creation because pod was not started[/yellow]"
        )
    else:
        # Let's confirm mysql is running
        for system_pod in CONFIG.get("system-pods", []):
            if system_pod["pod"]["name"] == "mysql":
                # Let's wait for the MySQL pod to start
                if utils.wait_for_pod_status("mysql", "Running"):
                    rprint("       [green]MySQL running")

                    # Check for actual connectivity via socket before proceeding
                    if not utils.wait_for_mysql_socket():
                        rprint(
                            "       [red]MySQL failed to respond on socket after waiting."
                        )
                        return

    # Create the databases requested in each of the pods
    for pod in CONFIG.get("pods", []):
        if isinstance(pod, dict) and "repo" in pod:
            pod_name = pod["repo"].split("/")[-1:][0].replace(".git", "")
        else:
            pod_name = pod

        pod_config = utils.get_pod_config(pod_name)
        _process_pod_databases(pod_config)


def connect_to_mysql() -> None:
    """Connect to the MySQL cluster inside the k3s cluster"""
    utils.connect_to_db()


def connect_to_postgres() -> None:
    """Connect to the PostgreSQL cluster inside the k3s cluster"""
    utils.connect_to_db_postgres()


def connect_to_minio() -> None:
    """Open a port=forward and print a nice message to inform user"""
    rprint("Open a browser and visit: http://127.0.0.1:9090/")
    rprint("Press ctrl+c to exit\n")
    rprint("Username: minio")
    rprint("Password: minio123\n")
    utils.connect_to_minio()


def seed_pod(pod):
    """Run the seeddb.py script inside a pod's container"""
    config = utils.get_pod_config(pod)
    seed_command = config["seed-command"]
    utils.run_command_inside_pod(pod, seed_command)
    rprint(f"  -- {pod} database seeded")


def init_pod_db(pod):
    """Run the initdb.py script inside a pod's container"""
    config = utils.get_pod_config(pod)
    init_command = config["init-command"]
    utils.run_command_inside_pod(pod, init_command)
    rprint(f"  -- {pod} database initialized")
