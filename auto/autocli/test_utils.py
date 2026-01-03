"""Tests for auto.autocli.utils"""

import subprocess
from unittest.mock import MagicMock, mock_open, patch

from autocli import utils


@patch("autocli.utils.Confirm.ask")
@patch("os.makedirs")
@patch("os.path.exists")
@patch("os.path.isfile")
@patch("builtins.open", new_callable=mock_open, read_data="code: /tmp/code")
@patch("yaml.safe_load")
def test_load_config(
    mock_yaml, _mock_file, mock_isfile, mock_exists, mock_makedirs, mock_confirm
):
    """Test loading configuration"""
    # Setup default yaml return
    mock_yaml.return_value = {"code": "/tmp/code"}

    # Case 1: Config exists and code folder exists
    mock_isfile.return_value = True
    mock_exists.return_value = True

    config = utils.load_config()
    assert config["code"] == "/tmp/code"

    # Case 2: Config exists but code folder MISSING (User says YES to create)
    mock_exists.return_value = False  # Folder missing
    mock_confirm.return_value = True  # User says Yes

    config = utils.load_config()
    assert config["code"] == "/tmp/code"
    mock_makedirs.assert_called_with("/tmp/code")

    # Case 3: Config does not exist (should trigger error/create)
    # We mock declare_error to avoid sys.exit
    mock_isfile.return_value = False
    with patch("autocli.utils.declare_error") as mock_error:
        with patch("autocli.utils.create_initial_config") as mock_create:
            utils.load_config()
            mock_error.assert_called()
            mock_create.assert_called()


@patch("subprocess.run")
def test_run_and_wait_success(mock_run):
    """Test successful command execution"""
    mock_run.return_value = MagicMock(returncode=0, stdout="success\n")

    # Test simple run
    result = utils.run_and_wait("echo test")
    assert result == 1
    mock_run.assert_called_with(
        "echo test", capture_output=True, shell=True, check=True, cwd=None
    )


@patch("subprocess.run")
def test_run_and_wait_check_result(mock_run):
    """Test checking output result"""
    mock_run.return_value = MagicMock(returncode=0, stdout="found me\n")

    # Test check_result logic
    result = utils.run_and_wait("echo test", check_result="found")
    assert result == 1

    result = utils.run_and_wait("echo test", check_result="missing")
    assert result == 0


@patch("subprocess.run")
def test_run_and_wait_failure(mock_run):
    """Test command failure handling"""
    mock_run.side_effect = subprocess.CalledProcessError(1, "cmd", stderr=b"error")

    # Should return 0 on failure if suppressed or handled
    result = utils.run_and_wait("fail_cmd")
    assert result == 0


@patch("autocli.utils.run_and_wait")
def test_check_docker(mock_run):
    """Test docker dependency check"""
    # Case: All good
    mock_run.side_effect = [True, True, True]  # which, ps aux, docker ps
    errors = utils.check_docker()
    assert errors == 0

    # Case: Missing docker
    mock_run.side_effect = [False, False, False]
    with patch("autocli.utils.declare_error"):
        errors = utils.check_docker()
        assert errors == 3


@patch("subprocess.run")
def test_get_full_pod_name(mock_run):
    """Test getting full pod name"""
    mock_run.return_value = MagicMock(stdout=b"mypod-12345\n")

    name = utils.get_full_pod_name("mypod")
    assert name == "mypod-12345"
    assert "kubectl get pods" in mock_run.call_args[0][0][0]


@patch("os.getcwd", return_value="/tmp")
@patch("os.chdir")
@patch("os.path.exists")
@patch("autocli.utils.run_and_wait")
def test_pull_repo(mock_run, mock_exists, mock_chdir, _mock_getcwd):
    """Test pulling repositories"""
    repo = {"repo": "git@github.com:org/repo.git", "branch": "main"}

    # Case 1: Repo exists, no changes
    mock_exists.return_value = True
    # git status (clean), git pull
    mock_run.side_effect = [True, True]

    utils.pull_repo(repo, "/code")
    assert mock_chdir.call_count >= 2

    # Case 2: Repo does not exist, clone
    mock_exists.return_value = False
    mock_chdir.reset_mock()
    # git clone, git checkout
    mock_run.side_effect = [True, True]

    utils.pull_repo(repo, "/code")
    assert "git clone" in mock_run.call_args_list[2][0][0]


@patch("autocli.utils.get_full_pod_name")
@patch("subprocess.run")
def test_create_mysql_database(mock_run, mock_pod_name):
    """Test database creation with retries"""
    mock_pod_name.return_value = "mysql-pod"

    # Case: Success
    utils.create_mysql_database("mydb")
    mock_run.assert_called()

    # Case: Failure with retry
    # 1. Fail (CalledProcessError)
    # 2. Success
    mock_run.side_effect = [subprocess.CalledProcessError(1, "cmd"), MagicMock()]

    # Patch sleep to speed up test
    with patch("time.sleep"):
        utils.create_mysql_database("mydb", retries=0)

    assert mock_run.call_count == 3  # Initial + 1 fail + 1 success


@patch("autocli.utils.load_config")
@patch("os.path.isfile")
@patch("builtins.open", new_callable=mock_open, read_data="name: test\n")
@patch("yaml.safe_load")
def test_get_pod_config(mock_yaml, _mock_file, mock_isfile, mock_load_config):
    """Test fetching pod config"""
    mock_load_config.return_value = {"code": "/code"}
    mock_isfile.return_value = True
    mock_yaml.return_value = {"name": "test"}

    config = utils.get_pod_config("mypod")
    assert config["name"] == "test"

    # Case: File not found
    mock_isfile.return_value = False
    with patch("autocli.utils.declare_error") as mock_err:
        utils.get_pod_config("mypod")
        mock_err.assert_called()
