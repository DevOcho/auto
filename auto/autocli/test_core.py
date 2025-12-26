"""Tests for auto.autocli.core"""

from unittest.mock import MagicMock, mock_open, patch

from autocli import core


@patch("autocli.utils.run_and_wait")
@patch("autocli.utils.verify_cluster_connection")
def test_start_cluster_existing(mock_verify, mock_run):
    """Test starting an existing cluster"""
    # Mock mocks
    progress = MagicMock()
    task = MagicMock()

    # Scenario: k3d cluster list finds "k3s-default" (existing)
    # 1. k3d cluster list -> True (found)
    # 2. k3d kubeconfig merge -> True
    # 3. k3d cluster list (check running 0/1) -> False (already running)
    mock_run.side_effect = [True, True, False]
    mock_verify.return_value = True

    result = core.start_cluster(progress, task)
    assert result is False  # False means "not a new cluster"


@patch("autocli.utils.wait_for_pod_status")
@patch("autocli.utils.run_and_wait")
@patch("autocli.utils.verify_cluster_connection")
def test_start_cluster_new(mock_verify, mock_run, mock_wait):
    """Test creating a new cluster"""
    progress = MagicMock()
    task = MagicMock()

    # Setup returns
    mock_verify.return_value = True
    mock_wait.return_value = True

    # Define side effect for run_and_wait
    # 1. k3d cluster list (check existing) -> False
    # All subsequent calls should return True
    def side_effect(*args, **kwargs):
        cmd = args[0]
        # Check if we are looking for the existing cluster
        if (
            "cluster list" in cmd
            and "check_result" in kwargs
            and kwargs["check_result"] == "k3s-default"
        ):
            return False
        return True

    mock_run.side_effect = side_effect

    # Mock config to avoid key errors
    with patch.dict(core.CONFIG, {"code": "/tmp", "https": False}):
        result = core.start_cluster(progress, task)
        assert result is True  # True means "new cluster created"


@patch("pathlib.Path.is_file")
@patch("autocli.utils.run_and_wait")
def test_stop_pod_helm(mock_run, mock_is_file):
    """Test stopping a helm pod"""
    # Mock config loading for the pod
    pod_config = """
    command: helm install
    name: myrelease
    """

    # 1. kubectl get pods -> True (running)
    # 2. helm uninstall -> True
    mock_run.side_effect = [True, True]
    mock_is_file.return_value = True

    with patch("builtins.open", mock_open(read_data=pod_config)):
        with patch.dict(core.CONFIG, {"code": "/tmp"}):
            core.stop_pod("mypod")

    # Verify helm uninstall was called with release name
    # We check call_args_list to find the uninstall command
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

    # 1. kubectl get pods -> True
    # 2. kubectl delete -> True
    mock_run.side_effect = [True, True]
    mock_is_file.return_value = True

    with patch("builtins.open", mock_open(read_data=pod_config)):
        with patch.dict(core.CONFIG, {"code": "/tmp"}):
            core.stop_pod("mypod")

    # Verify kubectl delete was called with correct args and cwd
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
    # Case: Registry does NOT exist
    mock_run.return_value = False

    with patch("time.sleep"):
        core.start_registry()

    # Should attempt to create
    assert mock_run.call_count >= 2
    assert "registry create" in mock_run.call_args[0][0]


@patch("subprocess.run")
@patch("autocli.utils.run_and_wait")
def test_delete_cluster_success(mock_run_wait, mock_sub):
    """Test deleting cluster successfully"""
    progress = MagicMock()
    task = MagicMock()

    # Mock run_and_wait for the delete command
    mock_run_wait.return_value = True

    # Mock subprocess.run for the verification loop (k3d list and docker ps)
    # Both need to return "clean" (returncode 0, text not found)
    mock_k3d = MagicMock(returncode=0, stdout="")
    mock_docker = MagicMock(returncode=0, stdout="")
    mock_sub.side_effect = [mock_k3d, mock_docker]

    core.delete_cluster(progress, task)

    # Should update progress if successful
    progress.update.assert_called()


@patch("autocli.utils.run_and_return")
def test_list_cluster_images(mock_run_return):
    """Test listing images"""
    # Mock kubectl output
    mock_run_return.return_value = "k3d-registry.local:12345/mysql:8.0 nginx:alpine"

    # Mock config to have some local pods
    with patch.dict(core.CONFIG, {"pods": [{"repo": "git@github:org/portal.git"}]}):
        # Capture stdout to verify printing
        with patch("builtins.print"):
            core.list_cluster_images()

            # Check if mysql and nginx were printed
            # (Note: portal is filtered out if present, but here it isn't in output)
            # We are just checking if it processed the list
            assert mock_run_return.called


@patch("autocli.utils.run_and_return")
@patch("autocli.utils.get_full_pod_name")
@patch("os.system")
@patch("autocli.utils.run_and_wait")
def test_output_logs(mock_run_wait, mock_system, mock_name, mock_ip):
    """Test log output logic"""
    # Cluster running check: "0/1" (stopped) should NOT be found
    mock_run_wait.return_value = False

    mock_name.return_value = "mypod-12345"
    mock_ip.return_value = "10.0.0.5"

    core.output_logs("mypod")

    # Verify os.system was called with grep
    cmd = mock_system.call_args[0][0]
    assert "kubectl logs -f mypod-12345" in cmd
    assert "grep --line-buffered -v" in cmd
    assert "10.0.0.5" in cmd
