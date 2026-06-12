"""Tests for auto.autocli.core and auto.autocli.registry"""

# pylint: disable=protected-access,unused-argument

from unittest.mock import MagicMock, mock_open, patch

from autocli import core, registry
from autocli.config import CONFIG


@patch("autocli.utils.run_and_wait")
@patch("autocli.utils.verify_cluster_connection")
def test_start_cluster_existing(mock_verify, mock_run):
    """Test starting an existing cluster"""
    progress = MagicMock()
    task = MagicMock()

    mock_run.side_effect = [True, True, False]
    mock_verify.return_value = True

    result = core.start_cluster(progress, task)
    assert result is False


@patch("autocli.utils.wait_for_pod_status")
@patch("autocli.utils.run_and_wait")
@patch("autocli.utils.verify_cluster_connection")
def test_start_cluster_new(mock_verify, mock_run, mock_wait):
    """Test creating a new cluster"""
    progress = MagicMock()
    task = MagicMock()

    mock_verify.return_value = True
    mock_wait.return_value = True

    def side_effect(*args, **kwargs):
        cmd = args[0]
        if (
            "cluster list" in cmd
            and "check_result" in kwargs
            and kwargs["check_result"] == "k3s-default"
        ):
            return False
        return True

    mock_run.side_effect = side_effect

    with patch.dict(CONFIG, {"code": "/tmp", "https": False}):
        result = core.start_cluster(progress, task)
        assert result is True


@patch("pathlib.Path.is_file")
@patch("autocli.utils.run_and_wait")
def test_stop_pod_helm(mock_run, mock_is_file):
    """Test stopping a helm pod"""
    pod_config = """
    command: helm install
    name: myrelease
    """

    mock_run.side_effect = [True, True]
    mock_is_file.return_value = True

    with patch("builtins.open", mock_open(read_data=pod_config)):
        with patch.dict(CONFIG, {"code": "/tmp"}):
            core.stop_pod("mypod")

    found = False
    for call in mock_run.call_args_list:
        args, _ = call
        if "helm uninstall myrelease" in args[0]:
            found = True
            break
    assert found


@patch("pathlib.Path.is_file")
@patch("autocli.utils.run_and_wait")
def test_stop_pod_kubectl(mock_run, mock_is_file):
    """Test stopping a kubectl pod"""
    pod_config = """
    command: kubectl apply
    command_args: -f deployment.yaml
    """

    mock_run.side_effect = [True, True]
    mock_is_file.return_value = True

    with patch("builtins.open", mock_open(read_data=pod_config)):
        with patch.dict(CONFIG, {"code": "/tmp"}):
            core.stop_pod("mypod")

    found = False
    for call in mock_run.call_args_list:
        args, kwargs = call
        if "kubectl delete -f deployment.yaml" in args[0]:
            assert kwargs.get("cwd") == "/tmp/mypod"
            found = True
            break
    assert found


@patch("autocli.utils.run_and_wait")
def test_start_registry(mock_run):
    """Test registry startup"""
    mock_run.return_value = False

    with patch("time.sleep"):
        registry.start_registry()

    assert mock_run.call_count >= 2
    assert "registry create" in mock_run.call_args[0][0]


@patch("subprocess.run")
@patch("autocli.utils.run_and_wait")
def test_delete_cluster_success(mock_run_wait, mock_sub):
    """Test deleting cluster successfully"""
    progress = MagicMock()
    task = MagicMock()

    mock_run_wait.return_value = True
    mock_k3d = MagicMock(returncode=0, stdout="")
    mock_docker = MagicMock(returncode=0, stdout="")
    mock_sub.side_effect = [mock_k3d, mock_docker]

    core.delete_cluster(progress, task)
    progress.update.assert_called()


@patch("autocli.utils.run_and_return")
@patch("autocli.registry._get_local_pod_names")
def test_list_cluster_images(mock_local_pods, mock_run_return):
    """Test listing images inside the registry script"""
    mock_run_return.return_value = "k3d-registry.local:12345/mysql:8.0 nginx:alpine"
    mock_local_pods.return_value = []

    with patch("builtins.print"):
        registry.list_cluster_images()
        assert mock_run_return.called


@patch("autocli.utils.get_cluster_status")
@patch("autocli.utils.run_and_return")
@patch("autocli.utils.get_full_pod_name")
@patch("os.system")
@patch("autocli.utils.run_and_wait")
def test_output_logs(
    mock_run_wait,
    mock_system,
    mock_name,
    mock_ip,
    mock_cluster_status,
):
    """Test log output logic"""
    mock_cluster_status.return_value = ("Running", "green")
    mock_run_wait.return_value = False
    mock_name.return_value = "mypod-12345"
    mock_ip.return_value = "10.0.0.5"

    core.output_logs("mypod")

    cmd = mock_system.call_args[0][0]
    assert "kubectl logs -f mypod-12345" in cmd
    assert "grep --line-buffered -v" in cmd
    assert "10.0.0.5" in cmd


@patch("autocli.utils.declare_error")
@patch("autocli.utils.get_cluster_status")
@patch("autocli.utils.get_full_pod_name")
@patch("os.system")
@patch("autocli.utils.run_and_wait")
def test_output_logs_cluster_down_missing_pod(
    mock_run_wait,
    mock_system,
    mock_get_name,
    mock_cluster_status,
    mock_declare,
):
    """Test output_logs reports missing pod even when cluster is down"""
    mock_cluster_status.return_value = ("Stopped", "red")
    mock_get_name.return_value = ""

    core.output_logs("blabla")

    mock_declare.assert_called_once()
    assert "Pod not found: blabla" in mock_declare.call_args[0][0]
    assert "Development cluster is not running." in mock_declare.call_args[0][0]
    assert not mock_system.called


# --- bootstrap_cluster: single-pod path ----------------------------------


@patch("autocli.registry.cache_running_images")
@patch("autocli.core.start_pod")
@patch("autocli.services.create_databases_for_pod")
@patch("autocli.services.install_system_pods")
@patch("autocli.core._prepare_single_pod")
@patch("autocli.core.verify_dependencies")
@patch("autocli.utils.get_cluster_status")
def test_bootstrap_single_pod_cluster_running(  # pylint: disable=too-many-arguments
    mock_status,
    mock_verify,
    mock_prepare,
    mock_install_sys,
    mock_create_db,
    mock_start,
    mock_cache,
):
    """Single-pod start runs the per-pod sequence when cluster is up."""
    mock_status.return_value = ("Running", "green")

    with patch.dict(CONFIG, {"pods": [], "https": False}, clear=False):
        core.bootstrap_cluster("api", dry_run=False, offline=False)

    mock_verify.assert_called_once()
    mock_prepare.assert_called_once_with("api", False)
    mock_install_sys.assert_called_once()
    mock_start.assert_called_once_with("api")
    mock_create_db.assert_called_once_with("api")
    # External images the new pod pulled get cached, matching the full start.
    mock_cache.assert_called_once()


@patch("autocli.registry.cache_running_images")
@patch("autocli.core.start_pod")
@patch("autocli.services.create_databases_for_pod")
@patch("autocli.services.install_system_pods")
@patch("autocli.core._prepare_single_pod")
@patch("autocli.core.verify_dependencies")
@patch("autocli.utils.get_cluster_status")
def test_bootstrap_single_pod_db_created_before_pod_starts(  # pylint: disable=too-many-arguments
    mock_status,
    mock_verify,
    mock_prepare,
    mock_install_sys,
    mock_create_db,
    mock_start,
    mock_cache,
):
    """Databases/buckets must be created BEFORE the pod starts so it doesn't
    crash-loop against a database that doesn't exist yet."""
    mock_status.return_value = ("Running", "green")

    # Attach the ordering-sensitive calls to one manager so we can assert order.
    manager = MagicMock()
    manager.attach_mock(mock_install_sys, "install_system_pods")
    manager.attach_mock(mock_create_db, "create_databases_for_pod")
    manager.attach_mock(mock_start, "start_pod")

    with patch.dict(CONFIG, {"pods": [], "https": False}, clear=False):
        core.bootstrap_cluster("api", dry_run=False, offline=False)

    ordered = [c[0] for c in manager.mock_calls]
    assert ordered.index("create_databases_for_pod") < ordered.index("start_pod")
    assert ordered.index("install_system_pods") < ordered.index(
        "create_databases_for_pod"
    )


@patch("autocli.core.start_pod")
@patch("autocli.services.create_databases_for_pod")
@patch("autocli.services.install_system_pods")
@patch("autocli.core._prepare_single_pod")
@patch("autocli.core.verify_dependencies")
@patch("autocli.utils.declare_error", side_effect=SystemExit)
@patch("autocli.utils.get_cluster_status")
def test_bootstrap_single_pod_cluster_down_errors(  # pylint: disable=too-many-arguments
    mock_status,
    mock_declare,
    mock_verify,
    mock_prepare,
    mock_install_sys,
    mock_create_db,
    mock_start,
):
    """Single-pod start fails fast when cluster is not running."""
    mock_status.return_value = ("Stopped", "red")

    with patch.dict(CONFIG, {"pods": [], "https": False}, clear=False):
        try:
            core.bootstrap_cluster("api", dry_run=False, offline=False)
        except SystemExit:
            pass

    # verify_dependencies runs before the cluster-status gate
    mock_verify.assert_called_once()
    mock_declare.assert_called_once()
    err_msg = mock_declare.call_args[0][0]
    assert "Cluster is not running" in err_msg
    # None of the per-pod work should have happened
    mock_prepare.assert_not_called()
    mock_install_sys.assert_not_called()
    mock_start.assert_not_called()
    mock_create_db.assert_not_called()


@patch("autocli.core.start_pod")
@patch("autocli.services.create_databases_for_pod")
@patch("autocli.services.install_system_pods")
@patch("autocli.core._prepare_single_pod")
@patch("autocli.core.verify_dependencies")
@patch("autocli.utils.get_cluster_status")
def test_bootstrap_single_pod_dry_run(  # pylint: disable=too-many-arguments
    mock_status, mock_verify, mock_prepare, mock_install_sys, mock_create_db, mock_start
):
    """--dry-run skips every side-effecting step on the single-pod path."""
    with patch.dict(CONFIG, {"pods": [], "https": False}, clear=False):
        core.bootstrap_cluster("api", dry_run=True, offline=False)

    mock_status.assert_not_called()
    mock_verify.assert_not_called()
    mock_prepare.assert_not_called()
    mock_install_sys.assert_not_called()
    mock_create_db.assert_not_called()
    # start_pod is the lone "no-op when dry-run is true" call that bootstrap
    # delegates the dry-run check to (it runs regardless), so we only assert
    # the side-effecting helpers are bypassed.
    mock_start.assert_called_once_with("api")


# --- migrate / rollback ---------------------------------------------------


@patch("autocli.utils.run_one_shot_pod_command")
def test_migrate_uses_ephemeral_pod(mock_run):
    """migrate should run smalls.py in a one-shot pod, not via kubectl exec."""
    mock_run.return_value = 0
    core.migrate_with_smalls("api")
    mock_run.assert_called_once()
    kwargs = mock_run.call_args.kwargs
    args = mock_run.call_args.args
    assert args[0] == "api"
    assert kwargs["command_args"] == ["/mnt/code/api/smalls.py", "migrate"]
    assert kwargs["action_label"] == "migrate"
    # SMALLS_ENV=PROD suppresses the interactive failure prompt
    assert {"name": "SMALLS_ENV", "value": "PROD"} in kwargs["extra_env"]


@patch("autocli.utils.run_command_inside_pod")
def test_rollback_stays_on_kubectl_exec(mock_exec):
    """rollback stays on kubectl exec because smalls.py prompts for confirmation."""
    core.rollback_with_smalls("api", "0003")
    mock_exec.assert_called_once_with("api", "./smalls.py rollback 0003")


# --- HTTPS ingress discovery ----------------------------------------------


@patch("autocli.utils.run_and_return")
def test_discover_ingress_hosts_dedupes_and_sorts(mock_return):
    """All ingress hosts are collected, de-duplicated, and returned sorted."""
    # A single pod exposing two hosts, with the same host repeated in tls.
    mock_return.return_value = (
        "portal.new-d8.local\nnew-d8.local\nportal.new-d8.local\n"
    )
    assert core._discover_ingress_hosts() == ["new-d8.local", "portal.new-d8.local"]
    assert "kubectl get ingress --all-namespaces" in mock_return.call_args[0][0]


@patch("autocli.utils.run_and_return")
def test_discover_ingress_hosts_empty(mock_return):
    """No ingresses (or a failed query) yields an empty list, not a crash."""
    mock_return.return_value = ""
    assert core._discover_ingress_hosts() == []


@patch("autocli.core._warn_missing_host_entries")
@patch("autocli.core._update_tls_secrets")
@patch("autocli.utils.create_local_certs")
@patch("autocli.core._discover_ingress_hosts")
def test_refresh_https_reissues_cert_for_all_hosts(
    mock_discover, mock_certs, mock_update, mock_warn
):
    """The cert is re-issued with every discovered host in the SAN list."""
    mock_discover.return_value = ["new-d8.local", "portal.new-d8.local"]
    mock_certs.return_value = ("key.pem", "cert.pem")

    core._refresh_https_for_ingresses()

    assert mock_certs.call_args.kwargs["additional_domains"] == [
        "new-d8.local",
        "portal.new-d8.local",
    ]
    mock_update.assert_called_once_with("key.pem", "cert.pem")
    mock_warn.assert_called_once_with(["new-d8.local", "portal.new-d8.local"])


@patch("autocli.core._update_tls_secrets")
@patch("autocli.utils.create_local_certs")
@patch("autocli.core._discover_ingress_hosts")
def test_refresh_https_noop_without_hosts(mock_discover, mock_certs, mock_update):
    """With no ingresses found, nothing is re-issued or applied."""
    mock_discover.return_value = []

    core._refresh_https_for_ingresses()

    mock_certs.assert_not_called()
    mock_update.assert_not_called()


@patch("autocli.utils.run_and_return")
def test_warn_missing_host_entries_reports_only_missing(mock_return):
    """Only hosts absent from /etc/hosts are flagged."""
    mock_return.return_value = "127.0.0.1 new-d8.local\n"
    with patch("autocli.core.rprint") as mock_print:
        core._warn_missing_host_entries(["new-d8.local", "portal.new-d8.local"])

    # Only the missing host gets its own "127.0.0.1 <host>" suggestion line.
    host_lines = [
        str(call.args[0])
        for call in mock_print.call_args_list
        if "127.0.0.1" in str(call.args[0])
    ]
    assert len(host_lines) == 1
    assert host_lines[0].endswith("portal.new-d8.local")
