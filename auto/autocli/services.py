"""System Pods and Database Services Management"""

import concurrent.futures
import shlex
import subprocess
import time
from subprocess import CalledProcessError

from autocli import runner, utils
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
        "redis": {"host": 6379, "lb": 30037, "desc": "Redis"},
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
            setup_minio()


def _process_mysql_databases(system_pod):
    """Helper to process MySQL database creation"""
    for database in system_pod.get("databases", []):
        create_mysql_database(database["name"])
        rprint(f"      *  Created MySQL database:[bright_cyan]{database['name']}")


def _process_minio_buckets(system_pod):
    """Helper to process MinIO bucket creation"""
    for bucket in system_pod.get("buckets", []):
        create_minio_bucket(bucket["name"])
        rprint(f"      *  Created MinIO bucket:[bright_cyan]{bucket['name']}")


def _process_postgres_databases(system_pod):
    """Helper to process Postgres database creation"""
    for database in system_pod.get("databases", []):
        create_postgres_database(database["name"])
        rprint(f"      *  Created Postgres database:[bright_cyan]{database['name']}")


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
        elif system_pod.get("name") == "postgres":
            _process_postgres_databases(system_pod)
        elif system_pod.get("name") == "minio":
            _process_minio_buckets(system_pod)


def _verify_db_system_ready(db_name, friendly_name, socket_check_func):
    """Helper to verify a system database pod is ready before creating databases"""
    # If the user port-blocked the launch earlier, let's fail gracefully here.
    if db_name in CONFIG.get("skipped-system-pods", []):
        rprint(
            f"       [yellow]Skipping {friendly_name} database creation because pod was not started[/yellow]"
        )
        return True

    # Let's confirm the database is running
    for system_pod in CONFIG.get("system-pods", []):
        if system_pod["pod"]["name"] == db_name:
            # Let's wait for the pod to start
            if utils.wait_for_pod_status(db_name, "Running"):
                rprint(f"       [green]{friendly_name} running")

                # Check for actual connectivity via socket before proceeding
                if not socket_check_func():
                    rprint(
                        f"       [red]{friendly_name} failed to respond on socket after waiting."
                    )
                    return False
    return True


def _verify_required_dbs_ready(needs):
    """Verify only the system DB pods referenced by `needs` are ready.

    `needs` is a set of system-pod names (e.g. {'mysql', 'postgres'}). Returns
    True if all required DB pods are ready (or none were required).
    """
    checks = {
        "mysql": ("MySQL", wait_for_mysql_socket),
        "postgres": ("Postgres", wait_for_postgres_socket),
    }
    targets = {name: checks[name] for name in needs if name in checks}

    if not targets:
        return True

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(targets)) as executor:
        futures = {
            name: executor.submit(_verify_db_system_ready, name, friendly, sock_fn)
            for name, (friendly, sock_fn) in targets.items()
        }
        return all(future.result() for future in futures.values())


def create_databases():
    """Create the databases"""
    rprint("  -- Creating Databases and Buckets")

    # Check MySQL and Postgres readiness in parallel so both pods warm up simultaneously
    if not _verify_required_dbs_ready({"mysql", "postgres"}):
        return

    # Create the databases requested in each of the pods
    for pod in CONFIG.get("pods", []):
        if isinstance(pod, dict) and "repo" in pod:
            pod_name = pod["repo"].split("/")[-1:][0].replace(".git", "")
        else:
            pod_name = pod

        pod_config = utils.get_pod_config(pod_name)
        _process_pod_databases(pod_config)


def create_databases_for_pod(pod_name):
    """Create databases and buckets declared by a single pod's .auto/config.yaml.

    Used by the single-pod `auto start <pod>` path so that newly added
    databases or MinIO buckets are picked up without a full cluster bootstrap.
    All underlying create_* helpers are idempotent, so existing resources
    are left untouched.
    """
    pod_config = utils.get_pod_config(pod_name)
    if not pod_config.get("system-pods"):
        return

    needs = {sp.get("name") for sp in pod_config["system-pods"] if sp.get("name")}

    rprint("  -- Creating Databases and Buckets")

    if not _verify_required_dbs_ready(needs):
        return

    _process_pod_databases(pod_config)


def connect_to_mysql() -> None:
    """Connect to the MySQL cluster inside the k3s cluster"""
    _connect_to_db()


def connect_to_postgres() -> None:
    """Connect to the PostgreSQL cluster inside the k3s cluster"""
    _connect_to_db_postgres()


def connect_to_minio() -> None:
    """Open a port=forward and print a nice message to inform user"""
    rprint("Open a browser and visit: http://127.0.0.1:9090/")
    rprint("Press ctrl+c to exit\n")
    rprint("Username: minio")
    rprint("Password: minio123\n")
    _connect_to_minio()


def connect_to_mailpit() -> None:
    """Open a port-forward to the Mailpit inbox and tell the user where to look"""
    rprint("Open a browser and visit: http://127.0.0.1:8025/")
    rprint("(the ingress also serves it at http://mailpit.local/)")
    rprint("Press ctrl+c to exit\n")
    _connect_to_mailpit()


def seed_pod(pod):
    """Run the seed script in an ephemeral pod that mirrors the deployment"""
    config = utils.get_pod_config(pod)
    seed_command = config["seed-command"]
    rc = runner.run_one_shot_pod_command(
        pod,
        command_args=[f"/mnt/code/{pod}/{seed_command}"],
        action_label="seed",
    )
    if rc == 0:
        rprint(f"  -- {pod} database seeded")


def init_pod_db(pod):
    """Run the initdb script in an ephemeral pod that mirrors the deployment"""
    config = utils.get_pod_config(pod)
    init_command = config["init-command"]
    rc = runner.run_one_shot_pod_command(
        pod,
        command_args=[f"/mnt/code/{pod}/{init_command}"],
        action_label="init",
    )
    if rc == 0:
        rprint(f"  -- {pod} database initialized")


# --- Low-level database / object-store operations -------------------------
# These exec directly against the system pods (MySQL, Postgres, MinIO). They
# are idempotent and retry on startup races so the orchestration helpers above
# can call them freely.


def wait_for_mysql_socket(retries=30) -> bool:
    """Wait for MySQL socket to be available inside the pod"""
    pod_name = utils.get_full_pod_name("mysql").strip("\n")
    if not pod_name:
        return False

    for _ in range(retries):
        # We use a real query to test connectivity, not just admin ping
        cmd = f'kubectl exec {pod_name} -- mysql -uroot -ppassword -e "SELECT 1"'
        try:
            subprocess.run(cmd, capture_output=True, shell=True, check=True)
            return True
        except CalledProcessError:
            time.sleep(1)
    return False


def wait_for_postgres_socket(retries=30) -> bool:
    """Wait for Postgres socket to be available inside the pod"""
    pod_name = utils.get_full_pod_name("postgres").strip("\n")
    if not pod_name:
        return False

    for _ in range(retries):
        # We use a real query to test connectivity
        cmd = f'kubectl exec {pod_name} -- psql -U root -d postgres -c "SELECT 1"'
        try:
            subprocess.run(cmd, capture_output=True, shell=True, check=True)
            return True
        except CalledProcessError:
            time.sleep(1)
    return False


def create_postgres_database(database, retries=0):
    """Create a database inside postgres"""
    # We use a quick bash command to see if the DB exists, and create it if it doesn't.
    # This prevents Postgres from throwing errors on subsequent "auto start" runs.
    container_cmd = f'sh -c "psql -U root -lqt | grep -qw {database} || createdb -U root {database}"'
    pod_name = utils.get_full_pod_name("postgres").strip("\n")

    if pod_name:
        cmd = f"kubectl exec {pod_name} -- {container_cmd}"

        try:
            # Run the command silently
            utils.run_silent(cmd)
        except CalledProcessError:
            if retries < 10:  # Allow up to 30s for slower startups
                time.sleep(3)
                create_postgres_database(database, retries=retries + 1)
            else:
                rprint(f"  [red]FAILED: Could not create database[/] {database}")

    else:
        # If pod_name not found, wait and retry
        if retries < 10:
            time.sleep(3)
            create_postgres_database(database, retries=retries + 1)
        else:
            rprint(f"  [red]FAILED: Could not create database[/] {database}")


def create_mysql_database(database, retries=0):
    """Create a database inside mysql"""

    # IF NOT EXISTS prevents a failed retry loop when the database already exists
    container_cmd = (
        f'mysql -uroot -ppassword --execute="CREATE DATABASE IF NOT EXISTS {database}"'
    )
    pod_name = utils.get_full_pod_name("mysql").strip("\n")

    if pod_name:
        cmd = f"kubectl exec {pod_name} -- {container_cmd}"

        try:
            # Run the command silently.
            # We suppress output to hide "ERROR 2002" messages during startup.
            utils.run_silent(cmd)
        except CalledProcessError:
            if retries < 10:  # Allow up to 30s for slower startups
                time.sleep(3)
                create_mysql_database(database, retries=retries + 1)
            else:
                rprint(f"  [red]FAILED: Could not create database[/] {database}")

    else:
        # If pod_name not found, wait and retry
        if retries < 10:
            time.sleep(3)
            create_mysql_database(database, retries=retries + 1)
        else:
            rprint(f"  [red]FAILED: Could not create database[/] {database}")


def create_minio_bucket(bucket):
    """Create a bucket in MinIO"""

    pod_name = utils.get_full_pod_name("minio").strip("\n")

    if pod_name:
        # Batch all three mc commands into a single exec call to avoid subprocess overhead per bucket
        combined = (
            f"mc mb --quiet myminio/{bucket} ; "  # disable file list
            f"mc anonymous --quiet set none myminio/{bucket} && "  # enable full path access
            f"mc anonymous --quiet set download myminio/{bucket}/*"
        )
        cmd = f"kubectl exec {pod_name} -- sh -c {shlex.quote(combined)}"
        utils.run_silent(cmd, merge_stderr=True)


def setup_minio(retries=5):
    """Setup the credentials and configure and deploy nginx"""

    container_cmds = [
        "mc alias -q set myminio http://minio.default.svc.cluster.local:9000 minio minio123"
    ]
    pod_name = utils.get_full_pod_name("minio").strip("\n")

    if pod_name:
        # Let's run the commands in the container to setup the access creds
        for container_cmd in container_cmds:
            full_cmd = f"kubectl exec -it {pod_name} -- {container_cmd}"

            # Run the command silently
            utils.run_silent(full_cmd, merge_stderr=True)

    else:
        if retries > 1:
            time.sleep(3)
            setup_minio(retries - 1)


def _connect_to_db() -> None:
    """Open an interactive mysql shell inside the MySQL system pod"""

    # The command we will send to the mysql pod
    container_cmd = "mysql -uroot -ppassword"

    # Determine which pod to exec against and build the command
    pod_name = utils.get_full_pod_name("mysql").strip("\n")
    cmd = f"kubectl exec -it {pod_name} -- {container_cmd}"

    # Make this command safe to run
    cmd = shlex.quote(cmd)
    args = shlex.split(cmd)

    # Run the command and return the output
    subprocess.run(args, shell=True, check=True)


def _connect_to_db_postgres() -> None:
    """Open an interactive psql shell inside the Postgres system pod"""

    # The command we will send to the postgres pod
    container_cmd = "psql -U root postgres"

    # Determine which pod to exec against and build the command
    pod_name = utils.get_full_pod_name("postgres").strip("\n")
    cmd = f"kubectl exec -it {pod_name} -- {container_cmd}"

    # Make this command safe to run
    cmd = shlex.quote(cmd)
    args = shlex.split(cmd)

    # Run the command and return the output
    subprocess.run(args, shell=True, check=True)


def _connect_to_mailpit() -> None:
    """This opens the port-forward to the Mailpit web UI to allow dev access"""

    # Determine which pod to exec against and build the command
    pod_name = utils.get_full_pod_name("mailpit").strip("\n")

    # 8025 is Mailpit's web UI; 1025 (SMTP) stays in-cluster where pods use it
    cmd = f"kubectl port-forward {pod_name} 8025:8025"

    # Make this command safe to run
    cmd = shlex.quote(cmd)
    args = shlex.split(cmd)

    # Run the command and return the output
    subprocess.run(args, shell=True, check=True)


def _connect_to_minio() -> None:
    """This opens the port-forward to MinIO to allow dev access"""

    # Determine which pod to exec against and build the command
    pod_name = utils.get_full_pod_name("minio").strip("\n")

    # The command we are going to run
    cmd = f"kubectl port-forward {pod_name} 9090:9090"

    # Make this command safe to run
    cmd = shlex.quote(cmd)
    args = shlex.split(cmd)

    # Run the command and return the output
    subprocess.run(args, shell=True, check=True)
