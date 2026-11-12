"""System Pods and Database Services Management"""

import concurrent.futures
import os
import shlex
import subprocess
import time
from subprocess import CalledProcessError

from rich import print as rprint

from autocli import registry, runner, utils
from autocli.config import CONFIG


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
        except Exception:  # noqa: BLE001
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
        "k3d node edit k3d-k3s-default-serverlb "
        f"--port-add {utils.bind_address()}:{host_port}:{lb_port}"
    )
    return True


# Manifests auto no longer ships: HTTP routes to the MySQL, Postgres and Redis
# ports, which never served anything. Existing local.yaml files still list
# them, and installing a release never deletes old files from ~/.auto/k3s, so
# stale copies would otherwise keep being applied on every start.
RETIRED_MANIFESTS = {
    os.path.expanduser(f"~/.auto/k3s/{pod}/ingress.yaml")
    for pod in ("mysql", "postgres", "redis")
}


# System pods auto no longer ships. An old local.yaml can still list one, and
# its manifests stay behind in ~/.auto/k3s, so it is skipped instead of being
# started from those stale copies.
RETIRED_SYSTEM_PODS = {"matomo"}

# floci-aws is auto's S3. It replaced MinIO, which stopped publishing its image.
# A pod that still asks for `minio` gets floci-aws, plus MinIO's old Service name
# and hostname (minio-alias.yaml), so code written against MinIO keeps working
# unchanged. The `minio` entry an old local.yaml still has is never started: its
# manifests left in ~/.auto/k3s/minio name an image that can't be pulled.
S3_POD = "floci-aws"
REPLACED_SYSTEM_PODS = {"minio": S3_POD}
MINIO_ALIAS_MANIFEST = "~/.auto/k3s/floci/minio-alias.yaml"

# floci-aws's commands, for a local.yaml written before it shipped, so a pod
# asking for S3 still gets it.
FLOCI_COMMANDS = [
    f"kubectl apply -f ~/.auto/k3s/floci/{name}.yaml"
    for name in (
        "serviceaccount",
        "role",
        "rolebinding",
        "deployment",
        "service",
        "ingress",
    )
]

# The Kubernetes API server's proxy to floci-aws's port. kubectl, which auto
# already drives, can send S3 requests through it, so buckets need neither a
# port-forward nor a client in the pod (floci's image has no curl or AWS CLI).
FLOCI_PROXY = "/api/v1/namespaces/default/services/http:floci-aws:4566/proxy"


def _is_retired(command):
    """True for a `kubectl apply -f` of a manifest auto no longer ships"""
    words = command.split()
    return bool(words) and os.path.expanduser(words[-1]) in RETIRED_MANIFESTS


# Database ports reachable from the host: host port -> the NodePort behind k3d's
# load balancer.
PORT_MAPPINGS = {
    "mysql": {"host": 3306, "lb": 30036, "desc": "MySQL"},
    "postgres": {"host": 5432, "lb": 30035, "desc": "Postgres"},
    "mssql": {"host": 1433, "lb": 30034, "desc": "SQL Server"},
    "redis": {"host": 6379, "lb": 30037, "desc": "Redis"},
}


# The load balancer's own ports for the sites: 80 and 443 behind host 80 and
# 443 with HTTPS, or 80 behind host 8088 without it
SITE_LB_PORTS = ("80", "443")


def load_balancer_args():
    """k3d flags for the Kubernetes API and the sites.

    The API is published on bind-address, the sites on sites-bind-address,
    both localhost unless local.yaml opens them to the network. Sites are on
    80 and 443 with HTTPS, or on 8088 without it.
    """
    api, addr = utils.bind_address(), utils.sites_bind_address()
    sites = (
        f'-p "{addr}:80:80@loadbalancer" -p "{addr}:443:443@loadbalancer"'
        if CONFIG.get("https", False)
        else f'-p "{addr}:8088:80@loadbalancer"'
    )
    return f"--api-port {api}:6550 {sites}"


def warn_if_cluster_bound_elsewhere():
    """An existing cluster keeps the ports it was made with; say so if they differ."""
    utils.warn_if_bound_elsewhere(
        "k3d-k3s-default-serverlb",
        "The cluster",
        "auto stop --delete-cluster, then auto start",
        site_ports=SITE_LB_PORTS,
    )


def cluster_port_args():
    """k3d `-p` flags that map this start's database ports when the cluster is created.

    Adding a port to a running cluster (`k3d node edit --port-add`) recreates
    k3d's load balancer, 20-43 s per port measured; mapped at creation they
    cost nothing. A host port already taken on this machine is left out, and
    install_system_pods then skips that pod exactly as before.
    """
    required = utils.get_required_system_pods(CONFIG)
    addr = utils.bind_address()
    flags = []
    for name, mapping in PORT_MAPPINGS.items():
        host, lb_port = mapping["host"], mapping["lb"]
        if name in required and not utils.is_port_in_use(host):
            flags.append(f'-p "{addr}:{host}:{lb_port}@loadbalancer"')
    return " ".join(flags)


# Started first on a new cluster, before Envoy Gateway is installed: databases
# take longest to come up (MySQL's first boot runs about 20 s), and their
# manifests hold no routes, so they don't need the Gateway API types Envoy
# Gateway brings. mailpit's and floci's manifests do.
EARLY_SYSTEM_PODS = ("mysql", "postgres")

# System pods this run of auto has started or skipped already, so the pass
# after Envoy Gateway doesn't start them again or repeat their warnings.
_handled_this_run = set()


def install_system_pods(only=None):
    """Install the system pods this start needs; with `only`, just those of them"""

    # We need to know which ones to start (both configured explicitly or requested implicitly)
    required_pods = utils.get_required_system_pods(CONFIG)
    port_mappings = PORT_MAPPINGS

    wants_minio = "minio" in required_pods
    if wants_minio:
        required_pods.add(S3_POD)
        if only is None:
            rprint(
                f"  -- [yellow]minio is served by {S3_POD} now[/]: MinIO no longer publishes "
                f"its image. minio:9000 keeps working; ask for {S3_POD} instead of minio "
                "when you can."
            )

    # Let's start the ones that we find that are "active" or requested
    for sys_pod in CONFIG.get("system-pods", []):
        pod_name = sys_pod["pod"]["name"]

        # Proceed only if this pod was identified as required
        if pod_name not in required_pods or pod_name in REPLACED_SYSTEM_PODS:
            continue

        if (only is not None and pod_name not in only) or pod_name in _handled_this_run:
            continue
        _handled_this_run.add(pod_name)

        if pod_name in RETIRED_SYSTEM_PODS:
            rprint(
                f"  -- [yellow]Skipping {pod_name}[/]: auto no longer ships it. "
                f"Remove it from ~/.auto/config/local.yaml"
            )
            continue

        # Check port mappings and expose dynamically if necessary
        if pod_name in port_mappings:
            if not _expose_system_pod_port(pod_name, port_mappings[pod_name]):
                # If the port exposure failed (due to local collision), we skip installing
                continue

        _start_system_pod(pod_name, sys_pod["pod"]["commands"])

    if only is not None:
        return

    _warn_about_undefined_system_pods(required_pods)

    # A local.yaml written before floci-aws shipped has no entry for it
    if S3_POD in required_pods and S3_POD not in _handled_this_run:
        _handled_this_run.add(S3_POD)
        _start_system_pod(S3_POD, FLOCI_COMMANDS)

    if wants_minio:
        _run_command_with_retry(f"kubectl apply -f {MINIO_ALIAS_MANIFEST}")

    # Buckets wait for floci-aws where they are made (_process_s3_buckets), so
    # loading the system pods never blocks on it.


def _warn_about_undefined_system_pods(required_pods):
    """Name each system pod a pod asks for that local.yaml has no entry for

    Without this it is silently never started, and the pod that needs it fails
    later in a way that doesn't point here.
    """
    defined = {sys_pod["pod"]["name"] for sys_pod in CONFIG.get("system-pods", [])}
    # minio is served by floci-aws, which starts even without an entry
    undefined = required_pods - defined - set(REPLACED_SYSTEM_PODS) - {S3_POD}
    if not undefined:
        return
    requests = utils.system_pod_requests(CONFIG)
    for name in sorted(undefined):
        askers = requests.get(name, [])
        who = (
            f"{' and '.join(askers)} {'asks' if len(askers) == 1 else 'ask'}"
            if askers
            else "A pod asks"
        )
        rprint(
            f"  -- [yellow]{name} won't start[/]: {who} for it, but "
            "~/.auto/config/local.yaml has no system pod by that name"
        )


def _start_system_pod(pod_name, commands):
    """Apply a system pod's manifests"""
    rprint("  -- Starting: " + pod_name)
    # populate_registry skips the images of system pods that are off, and this
    # one may have just been turned on, or asked for by `auto start <pod>`.
    registry.load_missing_images(utils.manifest_images(commands))
    for command in commands:
        if not _is_retired(command):
            _run_command_with_retry(command)


def _process_mysql_databases(system_pod):
    """Helper to process MySQL database creation"""
    for database in system_pod.get("databases", []):
        if create_mysql_database(database["name"]):
            rprint(f"      *  Created MySQL database:[bright_cyan]{database['name']}")


def _process_s3_buckets(system_pod):
    """Create the buckets a pod asks for, under floci-aws or its old name minio"""
    buckets = [bucket["name"] for bucket in system_pod.get("buckets", [])]
    if not buckets or not wait_for_floci():
        return
    for bucket in buckets:
        if create_s3_bucket(bucket):
            rprint(f"      *  Created bucket:[bright_cyan]{bucket}")
        else:
            rprint(f"      [red]Could not create bucket[/] {bucket}")


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
        elif system_pod.get("name") in ("minio", S3_POD):
            _process_s3_buckets(system_pod)


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
            # Let's wait for the pod to start. Up to 3 minutes (360 half-second
            # cycles): on a fresh or busy cluster the pod can take over a minute
            # to reach Running, and carrying on before it does only turns into
            # "Could not create database" a little later.
            if not utils.wait_for_pod_status(db_name, "Running", max_wait_time=360):
                rprint(
                    f"       [red]{friendly_name} did not start, so its databases were "
                    "not created (check `kubectl get pods`, then rerun auto start)[/red]"
                )
                return False
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

    # Check MySQL and Postgres readiness in parallel so both pods warm up
    # simultaneously -- but only the ones this start runs. Waiting for one that
    # nothing asked for held every start for minutes and then skipped all the
    # databases and buckets, because that pod never comes up.
    if not _verify_required_dbs_ready(utils.get_required_system_pods(CONFIG)):
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
    databases or buckets are picked up without a full cluster bootstrap.
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


def connect_to_mailpit() -> None:
    """Open a port-forward to the Mailpit inbox and tell the user where to look"""
    rprint("Open a browser and visit: http://127.0.0.1:8025/")
    rprint("(the ingress also serves it at http://mailpit.local/)")
    rprint("Press ctrl+c to exit\n")
    _connect_to_mailpit()


def seed_pod(pod, seed_args=()):
    """Run the seed script in an ephemeral pod that mirrors the deployment.

    Extra arguments are passed through to the seed-command as-is.
    """
    config = utils.get_pod_config(pod)
    seed_command = config["seed-command"]
    rc = runner.run_one_shot_pod_command(
        pod,
        command_args=[f"/mnt/code/{pod}/{seed_command}", *seed_args],
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
# These exec directly against the system pods (MySQL, Postgres) or talk to
# floci-aws through the API server. They
# are idempotent and retry on startup races so the orchestration helpers above
# can call them freely.


def wait_for_mysql_socket(retries=120) -> bool:
    """Wait until MySQL answers over TCP inside the pod.

    The local socket is not enough: on first boot the image's entrypoint runs a
    temporary server with networking off, which answers on the socket and then
    restarts. A socket check passes against it, and CREATE DATABASE fails
    mid-restart. Only the real server listens on TCP, so 127.0.0.1 answering
    means MySQL is really up.
    """
    pod_name = utils.get_full_pod_name("mysql").strip("\n")
    if not pod_name:
        return False

    for _ in range(retries):
        # We use a real query to test connectivity, not just admin ping
        cmd = f'kubectl exec {pod_name} -- mysql -h127.0.0.1 -uroot -ppassword -e "SELECT 1"'
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

    # If pod_name not found, wait and retry
    elif retries < 10:
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
            return True
        except CalledProcessError:
            if retries < 10:  # Allow up to 30s for slower startups
                time.sleep(3)
                return create_mysql_database(database, retries=retries + 1)
            rprint(f"  [red]FAILED: Could not create database[/] {database}")
            return False

    # If pod_name not found, wait and retry
    if retries < 10:
        time.sleep(3)
        return create_mysql_database(database, retries=retries + 1)
    rprint(f"  [red]FAILED: Could not create database[/] {database}")
    return False


def wait_for_floci(retries=30):
    """Wait until floci-aws answers. It can be Running a few seconds before it does."""
    # A first start may still be pulling its image, so allow the pod 3 minutes
    utils.wait_for_pod_status(S3_POD, "Running", max_wait_time=360)
    for _ in range(retries):
        try:
            utils.run_silent(f"kubectl get --raw {FLOCI_PROXY}/_floci/health")
            return True
        except CalledProcessError:
            time.sleep(2)
    rprint(
        f"  [yellow]{S3_POD} did not answer, so its buckets may be missing (rerun auto start)[/]"
    )
    return False


def create_s3_bucket(bucket):
    """Create a bucket in floci-aws: an S3 CreateBucket (PUT /<bucket>, no body).

    Creating a bucket that exists succeeds and keeps its objects. floci serves
    objects to anonymous GETs, as MinIO's buckets did with their download policy.
    """
    try:
        utils.run_silent(
            f"kubectl replace --raw {shlex.quote(FLOCI_PROXY + '/' + bucket)} -f /dev/null",
            merge_stderr=True,
        )
        return True
    except CalledProcessError:
        return False


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
