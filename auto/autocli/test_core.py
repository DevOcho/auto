"""Tests for auto.autocli.core and auto.autocli.registry"""

import sys
from unittest.mock import MagicMock, mock_open, patch

import pytest

from autocli import core, https, registry
from autocli.config import CONFIG


@patch("autocli.utils.wait_for_api", return_value=True)
@patch("autocli.utils.nodes_without_dns", return_value=[])
@patch("autocli.utils.run_and_wait")
@patch("autocli.utils.verify_cluster_connection")
def test_start_cluster_existing(mock_verify, mock_run, mock_dns, mock_api):
    """Test starting an existing cluster"""
    progress = MagicMock()
    task = MagicMock()

    # cluster list, kubeconfig merge, "is it stopped?", then the Gateway check
    mock_run.side_effect = [True, True, False, True]
    mock_verify.return_value = True

    result = core.start_cluster(progress, task)
    assert result is False
    mock_dns.assert_called_once()
    mock_api.assert_called_once()
    gateway = f"kubectl get gateway {https.GATEWAY_NAME} -n {https.GATEWAY_NAMESPACE}"
    assert gateway in mock_run.call_args_list[-1].args[0]


@patch("autocli.utils.nodes_without_dns", return_value=[])
@patch("autocli.utils.run_and_wait")
def test_heal_dns_leaves_healthy_cluster_alone(mock_run, _mock_dns):
    """Nodes that resolve outside names don't get restarted"""
    core._verify_and_heal_dns()
    mock_run.assert_not_called()


@patch("autocli.utils.nodes_without_dns", return_value=None)
@patch("autocli.utils.run_and_wait")
def test_heal_dns_skips_restart_when_host_offline(mock_run, _mock_dns):
    """An offline host can't be fixed by restarting nodes, so don't try"""
    core._verify_and_heal_dns()
    mock_run.assert_not_called()


@patch("autocli.core.rprint")
@patch("autocli.utils.run_and_wait", return_value=1)
@patch("autocli.utils.nodes_without_dns")
def test_heal_dns_restarts_cluster_with_stale_dns(mock_dns, mock_run, mock_print):
    """Nodes that captured no DNS servers at boot get restarted, then rechecked.

    Regression test: Docker restarts the nodes before Wi-Fi is up, so every pod
    got SERVFAIL for outside names until someone restarted the cluster by hand.
    """
    mock_dns.side_effect = [["k3d-k3s-default-server-0"], []]

    core._verify_and_heal_dns()

    commands = [call.args[0] for call in mock_run.call_args_list]
    assert commands == ["k3d cluster stop", "k3d cluster start"]
    assert mock_dns.call_count == 2
    assert "restored" in str(mock_print.call_args_list[-1])


@patch("autocli.core.rprint")
@patch("autocli.utils.declare_error")
@patch("autocli.utils.run_and_wait", return_value=1)
@patch("autocli.utils.nodes_without_dns", return_value=["k3d-k3s-default-agent-0"])
def test_heal_dns_warns_without_failing_if_restart_does_not_help(
    _mock_dns, _mock_run, mock_err, mock_print
):
    """Still-broken DNS after a restart warns but doesn't stop the start"""
    core._verify_and_heal_dns()

    mock_err.assert_not_called()
    assert "still can't resolve" in str(mock_print.call_args_list[-1])


@patch("autocli.services.install_system_pods", new=lambda only=None: None)
@patch("autocli.services.cluster_port_args", new=lambda: '-p "3306:30036@loadbalancer"')
@patch("autocli.https._envoy_gateway_manifest", new=lambda: "/cache/eg.yaml")
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

    with patch.dict(
        CONFIG, {"code": "/tmp", "https": False, "bind-address": "127.0.0.1"}
    ):
        result = core.start_cluster(progress, task)
        assert result is True

    # New clusters get the pinned k3s, not k3d's default (too old for Envoy Gateway)
    create_cmd = next(
        c.args[0] for c in mock_run.call_args_list if "k3d cluster create" in c.args[0]
    )
    assert f"--image {https.K3S_IMAGE}" in create_cmd
    # Database ports are mapped up front, not added later with `k3d node edit`
    assert '-p "3306:30036@loadbalancer"' in create_cmd
    # The API and the sites listen on this machine only by default
    assert "--api-port 127.0.0.1:6550" in create_cmd
    assert '-p "127.0.0.1:8088:80@loadbalancer"' in create_cmd


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

    with patch("time.sleep"), patch.dict(CONFIG, {"bind-address": "127.0.0.1"}):
        registry.start_registry()

    assert mock_run.call_count >= 2
    assert "registry create" in mock_run.call_args[0][0]
    assert "--port 127.0.0.1:12345" in mock_run.call_args[0][0]


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


@patch("autocli.https.require_envoy_gateway", new=lambda: None)
@patch("autocli.https.warn_unrouted_ingresses", new=lambda: None)
@patch("autocli.registry.cache_running_images")
@patch("autocli.core.start_pod")
@patch("autocli.services.create_databases_for_pod")
@patch("autocli.services.install_system_pods")
@patch("autocli.core._prepare_single_pod")
@patch("autocli.core.verify_dependencies")
@patch("autocli.utils.get_cluster_status")
def test_bootstrap_single_pod_cluster_running(
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


@patch("autocli.https.require_envoy_gateway", new=lambda: None)
@patch("autocli.https.warn_unrouted_ingresses", new=lambda: None)
@patch("autocli.registry.cache_running_images")
@patch("autocli.core.start_pod")
@patch("autocli.services.create_databases_for_pod")
@patch("autocli.services.install_system_pods")
@patch("autocli.core._prepare_single_pod")
@patch("autocli.core.verify_dependencies")
@patch("autocli.utils.get_cluster_status")
def test_bootstrap_single_pod_db_created_before_pod_starts(
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
def test_bootstrap_single_pod_cluster_down_errors(
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
def test_bootstrap_single_pod_dry_run(
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


@patch("autocli.runner.run_one_shot_pod_command")
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


@patch("autocli.https.warn_unrouted_ingresses", new=lambda: None)
@patch("autocli.https.warn_missing_host_entries")
@patch("autocli.https.discover_ingress_hosts", return_value=["myapp.local"])
def test_print_access_hints_missing_host_shows_advisory_not_exit(
    mock_discover, mock_warn
):
    """A missing /etc/hosts entry is an advisory, never the end of a good start.

    check_host_entry defaults to exit_auto=True, so the old hint path ended a
    successful start with sys.exit once everything was already up.
    """
    pods = [{"repo": "git@github.com:user/myapp.git"}]

    with patch.object(
        sys, "exit", side_effect=AssertionError("sys.exit must not be called")
    ):
        core._print_access_hints(pods, use_https=False)

    mock_warn.assert_called_once_with(["myapp.local"])


@patch("autocli.utils.run_command_inside_pod")
def test_rollback_stays_on_kubectl_exec(mock_exec):
    """rollback stays on kubectl exec because smalls.py prompts for confirmation."""
    core.rollback_with_smalls("api", "0003")
    mock_exec.assert_called_once_with("api", "./smalls.py rollback 0003")


@patch("autocli.https.warn_unrouted_ingresses", new=lambda: None)
@patch("autocli.https.warn_missing_host_entries")
@patch("autocli.https.discover_ingress_hosts")
def test_access_hints_list_every_ingress_host(mock_discover, mock_warn):
    """System pods have ingresses too, so their hosts belong in the hints."""
    mock_discover.return_value = ["mailpit.local", "minio.local", "portal.local"]

    with patch("autocli.core.rprint") as mock_print:
        core._print_access_hints([{"repo": "git@host:org/portal.git"}], False)

    urls = [
        str(call.args[0])
        for call in mock_print.call_args_list
        if "://" in str(call.args[0])
    ]
    assert any("http://mailpit.local:8088/" in url for url in urls)
    assert len(urls) == 3

    # Plain HTTP has no cert refresh, so this is the only /etc/hosts nudge.
    mock_warn.assert_called_once_with(["mailpit.local", "minio.local", "portal.local"])


@patch("autocli.https.warn_unrouted_ingresses", new=lambda: None)
@patch("autocli.https.warn_missing_host_entries")
@patch("autocli.https.discover_ingress_hosts")
def test_access_hints_https_does_not_double_warn(mock_discover, mock_warn):
    """refresh_https_for_ingresses already warned, so don't repeat it here."""
    mock_discover.return_value = ["mailpit.local"]

    with patch("autocli.core.rprint") as mock_print:
        core._print_access_hints([], True)

    urls = [
        str(call.args[0])
        for call in mock_print.call_args_list
        if "://" in str(call.args[0])
    ]
    assert urls == ["[italic]  https://mailpit.local/"]
    mock_warn.assert_not_called()


@patch("autocli.https.warn_unrouted_ingresses", new=lambda: None)
@patch("autocli.https.warn_missing_host_entries")
@patch("autocli.https.discover_ingress_hosts")
def test_access_hints_fall_back_to_pod_names(mock_discover, mock_warn):
    """With no ingresses deployed (dry run) we still name the pods we started."""
    mock_discover.return_value = []

    with patch("autocli.core.rprint") as mock_print:
        core._print_access_hints([{"repo": "git@host:org/portal.git"}], False)

    urls = [
        str(call.args[0])
        for call in mock_print.call_args_list
        if "://" in str(call.args[0])
    ]
    assert urls == ["[italic]  http://portal.local:8088/"]


@patch("autocli.https.require_envoy_gateway", new=lambda: None)
@patch("autocli.https.warn_unrouted_ingresses", new=lambda: None)
@patch("autocli.registry.cache_running_images")
@patch("autocli.https.refresh_https_for_ingresses")
@patch("autocli.core.start_pod")
@patch("autocli.core.stop_pod")
@patch("autocli.utils.verify_pod_is_installed", return_value=False)
def test_restart_pod_refreshes_certs_and_caches_images(
    mock_installed, mock_stop, mock_start, mock_refresh, mock_cache
):
    """restart owes the same post-start work as start -- notably the cert refresh.

    Without it a pod whose ingress gained a hostname comes back up serving a
    certificate that doesn't cover it, with nothing in auto's output saying so.
    """
    with patch.dict(CONFIG, {"https": True}):
        core.restart_pod("portal")

    mock_stop.assert_called_once_with("portal")
    mock_start.assert_called_once_with("portal")
    mock_refresh.assert_called_once()
    mock_cache.assert_called_once()


@patch("autocli.core.stop_pod")
@patch("autocli.https.require_envoy_gateway", side_effect=SystemExit(1))
def test_restart_pod_checks_gateway_before_stopping(_mock_require, mock_stop):
    """On an old ingress-nginx cluster, restart stops before taking the pod down.

    The restarted pod's HTTPRoute would not even apply there, so stopping it
    first would leave the user with no pod at all.
    """
    with pytest.raises(SystemExit):
        core.restart_pod("portal")
    mock_stop.assert_not_called()


@patch("autocli.registry.cache_running_images")
@patch("autocli.https.warn_unrouted_ingresses")
@patch("autocli.https.refresh_https_for_ingresses")
def test_finish_pod_start_warns_about_unconverted_ingresses(
    _mock_refresh, mock_unrouted, _mock_cache
):
    """A pod still shipping `kind: Ingress` is named after it starts."""
    with patch.dict(CONFIG, {"https": True}):
        core.finish_pod_start()
    mock_unrouted.assert_called_once()


@patch("autocli.https.warn_unrouted_ingresses", new=lambda: None)
@patch("autocli.registry.cache_running_images")
@patch("autocli.https.refresh_https_for_ingresses")
def test_finish_pod_start_skips_cert_refresh_without_https(mock_refresh, mock_cache):
    """With https disabled there is no cert to re-issue, but images still cache."""
    with patch.dict(CONFIG, {"https": False}):
        core.finish_pod_start()

    mock_refresh.assert_not_called()
    mock_cache.assert_called_once()


@patch("autocli.utils.run_and_wait")
@patch("autocli.utils.get_pod_instances", return_value=["api-7d9f8c6b5-x2x9z", "api-0"])
def test_refresh_pod_deletes_every_instance(mock_instances, mock_run):
    """Every replica gets recreated so none keeps running the old image"""
    assert core.refresh_pod("api") is True
    mock_run.assert_called_once_with("kubectl delete pod api-7d9f8c6b5-x2x9z api-0")


@patch("autocli.utils.run_and_wait")
@patch("autocli.utils.get_pod_instances", return_value=[])
def test_refresh_pod_not_running(mock_instances, mock_run):
    """No matching pods means no bare `kubectl delete pod` with no name"""
    assert core.refresh_pod("api") is False
    mock_run.assert_not_called()


@patch("autocli.core.time.sleep")
def test_quit_key_pressed_without_terminal(mock_sleep):
    """With no terminal there's no key to read; just wait out the interval"""
    assert core._quit_key_pressed(3, interactive=False) is False
    mock_sleep.assert_called_once_with(3)


@patch("autocli.core.os.read", return_value=b"xQ")
@patch("autocli.core.select.select")
def test_quit_key_pressed_q(mock_select, mock_read):
    """Q (either case, even after another key) stops the watch right away"""
    mock_select.return_value = ([sys.stdin], [], [])
    with patch("autocli.core.sys.stdin") as mock_stdin:
        mock_stdin.fileno.return_value = 0
        assert core._quit_key_pressed(3, interactive=True) is True


@patch("autocli.core.os.read", return_value=b"x")
@patch("autocli.core.select.select")
def test_quit_key_pressed_other_key_keeps_waiting(mock_select, mock_read):
    """Any other key is ignored and the wait continues until the timeout"""
    mock_select.side_effect = [([sys.stdin], [], []), ([], [], [])]
    with (
        patch("autocli.core.sys.stdin") as mock_stdin,
        patch("autocli.core.time.monotonic", side_effect=[0, 1, 3]),
    ):
        mock_stdin.fileno.return_value = 0
        assert core._quit_key_pressed(3, interactive=True) is False
    assert mock_select.call_count == 2


@patch("autocli.core._quit_key_pressed", side_effect=[False, KeyboardInterrupt])
@patch("autocli.core.Live")
@patch("autocli.core.termios")
def test_watch_status_without_terminal(mock_termios, mock_live, mock_quit):
    """Piped stdin must not crash on termios, and Ctrl+C restores the cursor"""
    console = MagicMock()
    content = MagicMock()
    with patch("autocli.core.sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = False
        core._watch_status(console, content)

    mock_termios.tcgetattr.assert_not_called()
    mock_termios.tcsetattr.assert_not_called()
    mock_live.return_value.__enter__.return_value.update.assert_called_once()
    console.show_cursor.assert_called_once_with(True)


@patch("autocli.core._quit_key_pressed", return_value=True)
@patch("autocli.core.Live")
@patch("autocli.core.tty")
@patch("autocli.core.termios")
def test_watch_status_restores_terminal(mock_termios, mock_tty, mock_live, mock_quit):
    """Pressing Q puts the terminal back the way we found it"""
    console = MagicMock()
    with patch("autocli.core.sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = True
        core._watch_status(console, MagicMock())

    mock_tty.setcbreak.assert_called_once()
    mock_termios.tcsetattr.assert_called_once()
    assert mock_termios.tcsetattr.call_args[0][2] is mock_termios.tcgetattr.return_value
    console.show_cursor.assert_called_once_with(True)


@patch("autocli.https.apply_pod_routes")
@patch("autocli.core._execute_pod_install")
@patch(
    "autocli.core._build_install_command",
    return_value=("kubectl apply -f x", False, "api"),
)
@patch("autocli.utils.run_and_wait", return_value=False)
def test_start_pod_applies_routes_after_installing(
    _mock_run, _mock_build, mock_install, mock_routes, tmp_path
):
    """The pod's own install command runs first, then its .auto/routes.yaml."""
    (tmp_path / "api" / ".auto").mkdir(parents=True)
    (tmp_path / "api" / ".auto" / "config.yaml").write_text(
        "name: api\n", encoding="utf-8"
    )
    manager = MagicMock()
    manager.attach_mock(mock_install, "install")
    manager.attach_mock(mock_routes, "routes")

    with patch.dict(CONFIG, {"code": str(tmp_path)}):
        core.start_pod("api")

    assert [c[0] for c in manager.mock_calls] == ["install", "routes"]
    mock_routes.assert_called_once_with(str(tmp_path / "api"))


@patch("autocli.https.delete_pod_routes")
@patch("autocli.utils.run_and_wait", return_value=False)
def test_stop_pod_removes_routes_even_when_not_running(
    _mock_run, mock_routes, tmp_path
):
    """Routes come down with the pod, and are cleared even if the pod already stopped."""
    with patch.dict(CONFIG, {"code": str(tmp_path)}):
        core.stop_pod("api")
    mock_routes.assert_called_once_with(str(tmp_path / "api"))


@patch("autocli.core._verify_and_heal_connection", new=lambda: None)
@patch("autocli.services.cluster_port_args", new=lambda: "")
@patch("autocli.utils.run_and_wait")
def test_new_cluster_starts_its_databases_before_envoy_gateway(mock_run):
    """MySQL's first boot is the longest wait, so it starts before Envoy's install"""
    mock_run.side_effect = lambda cmd, **kw: (
        not ("cluster list" in cmd and kw.get("check_result") == "k3s-default")
    )
    order = MagicMock()
    with (
        patch("autocli.services.install_system_pods", order.system_pods),
        patch("autocli.https.install_envoy_gateway", order.envoy),
        patch.dict(CONFIG, {"code": "/tmp", "https": True}),
    ):
        assert core.start_cluster(MagicMock(), MagicMock(), "k.pem", "c.pem") is True
    assert [c[0] for c in order.mock_calls if c[0] in ("system_pods", "envoy")] == [
        "system_pods",
        "envoy",
    ]
    assert order.system_pods.call_args.kwargs == {"only": ("mysql", "postgres")}


@patch("autocli.core.verify_dependencies", new=lambda: None)
@patch("autocli.core.pull_and_build_pods", new=lambda: None)
@patch("autocli.registry.start_registry", new=lambda: None)
@patch("autocli.registry.populate_registry", new=lambda: None)
@patch("autocli.core.start_cluster", new=lambda *a, **kw: False)
@patch("autocli.core._install_system_sequence", new=lambda new_cluster: None)
@patch("autocli.core.install_pods_in_cluster", new=lambda: None)
@patch("autocli.https.finish_ingress", new=lambda use_https: None)
def test_urls_are_printed_before_images_are_cached():
    """The sites already work; caching only speeds up the next start"""
    order = MagicMock()
    with (
        patch("autocli.core._print_access_hints", order.hints),
        patch("autocli.registry.cache_running_images", order.cache),
        patch.dict(CONFIG, {"pods": [], "https": False}),
    ):
        core.bootstrap_cluster(None, dry_run=False, offline=False)
    assert [c[0] for c in order.mock_calls if c[0] in ("hints", "cache")] == [
        "hints",
        "cache",
    ]
