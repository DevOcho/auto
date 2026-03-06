"""Tests for auto.autocli.core and auto.autocli.registry"""

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


@patch("autocli.utils.run_and_return")
@patch("autocli.utils.get_full_pod_name")
@patch("os.system")
@patch("autocli.utils.run_and_wait")
def test_output_logs(mock_run_wait, mock_system, mock_name, mock_ip):
    """Test log output logic"""
    mock_run_wait.return_value = False
    mock_name.return_value = "mypod-12345"
    mock_ip.return_value = "10.0.0.5"

    core.output_logs("mypod")

    cmd = mock_system.call_args[0][0]
    assert "kubectl logs -f mypod-12345" in cmd
    assert "grep --line-buffered -v" in cmd
    assert "10.0.0.5" in cmd
