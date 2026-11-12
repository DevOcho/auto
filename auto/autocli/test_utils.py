"""Tests for auto.autocli.utils and auto.autocli.config"""

import subprocess
from unittest.mock import MagicMock, mock_open, patch

import yaml
from autocli import config, utils


@patch("autocli.config.Confirm.ask")
@patch("os.makedirs")
@patch("os.path.exists")
@patch("os.path.isfile")
@patch("builtins.open", new_callable=mock_open, read_data="code: /tmp/code")
@patch("yaml.safe_load")
def test_load_config(
    mock_yaml, _mock_file, mock_isfile, mock_exists, mock_makedirs, mock_confirm
):
    """Test loading configuration safely routes from config.py"""
    mock_yaml.return_value = {"code": "/tmp/code"}

    # Case 1: Config exists, code dir exists
    mock_isfile.return_value = True
    mock_exists.return_value = True

    res_config = config.load_config()
    assert res_config["code"] == "/tmp/code"

    # Case 2: Config exists, code folder MISSING (User says YES to create)
    mock_exists.return_value = False
    mock_confirm.return_value = True

    res_config = config.load_config()
    assert res_config["code"] == "/tmp/code"
    mock_makedirs.assert_called_with("/tmp/code")

    # Case 3: Config does not exist (should trigger create_initial_config)
    mock_isfile.return_value = False
    mock_exists.return_value = True
    with patch("autocli.config.create_initial_config") as mock_create:
        config.load_config()
        mock_create.assert_called()

    # Case 4: Code directory missing, user denies creation (triggers fatal error)
    mock_isfile.return_value = True
    mock_exists.return_value = False
    mock_confirm.return_value = False
    with patch("autocli.config._fatal_error") as mock_error:
        config.load_config()
        mock_error.assert_called_with("Code directory missing. Cannot proceed.")


@patch("subprocess.run")
def test_run_and_wait_success(mock_run):
    """Test successful command execution"""
    mock_run.return_value = MagicMock(returncode=0, stdout=b"success\n")

    result = utils.run_and_wait("echo test")
    assert result == 1
    mock_run.assert_called_with(
        "echo test", capture_output=True, shell=True, check=True, cwd=None
    )


@patch("subprocess.run")
def test_run_and_wait_check_result(mock_run):
    """Test checking output result"""
    mock_run.return_value = MagicMock(returncode=0, stdout=b"found me\n")

    result = utils.run_and_wait("echo test", check_result="found")
    assert result == 1

    result = utils.run_and_wait("echo test", check_result="missing")
    assert result == 0


@patch("subprocess.run")
def test_run_and_wait_failure(mock_run):
    """Test command failure handling"""
    mock_run.side_effect = subprocess.CalledProcessError(1, "cmd", stderr=b"error")

    result = utils.run_and_wait("fail_cmd")
    assert result == 0


@patch("autocli.utils.run_and_wait")
@patch("autocli.utils.declare_error")
def test_check_docker(mock_declare_error, mock_run):
    """Test docker dependency check"""
    mock_run.side_effect = [True, True, True]
    errors = utils.check_docker()
    assert errors == 0

    mock_run.side_effect = [False, False, False]
    errors = utils.check_docker()
    assert errors == 3
    mock_declare_error.assert_called()


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

    mock_exists.return_value = True
    mock_run.side_effect = [True, True]

    utils.pull_repo(repo, "/code")
    assert mock_chdir.call_count >= 2

    mock_exists.return_value = False
    mock_chdir.reset_mock()
    mock_run.side_effect = [True, True]

    utils.pull_repo(repo, "/code")
    assert "git clone" in mock_run.call_args_list[2][0][0]


@patch("autocli.utils.get_full_pod_name")
@patch("subprocess.run")
def test_create_mysql_database(mock_run, mock_pod_name):
    """Test database creation with retries"""
    mock_pod_name.return_value = "mysql-pod"

    utils.create_mysql_database("mydb")
    mock_run.assert_called()

    mock_run.side_effect = [subprocess.CalledProcessError(1, "cmd"), MagicMock()]
    with patch("time.sleep"):
        utils.create_mysql_database("mydb", retries=0)
    assert mock_run.call_count == 3


@patch("os.path.isfile")
@patch("builtins.open", new_callable=mock_open, read_data="name: test\n")
@patch("yaml.safe_load")
def test_get_pod_config(mock_yaml, _mock_file, mock_isfile):
    """Test fetching pod config mapped straight to global CONFIG object"""
    mock_isfile.return_value = True
    mock_yaml.return_value = {"name": "test"}

    # Simulate dynamically patched dictionary entry
    with patch.dict("autocli.config.CONFIG", {"code": "/code"}):
        res_config = utils.get_pod_config("mypod")
        assert res_config["name"] == "test"

    mock_isfile.return_value = False
    with patch("autocli.utils.declare_error") as mock_err:
        with patch.dict("autocli.config.CONFIG", {"code": "/code"}):
            utils.get_pod_config("mypod")
            mock_err.assert_called()


# --- one-shot pod helper --------------------------------------------------


def _fake_deployment(image="reg/api:1", env=None, volume_mounts=None, volumes=None):
    return {
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "api",
                            "image": image,
                            "env": env or [],
                            "volumeMounts": volume_mounts or [],
                            "workingDir": "/mnt/code/api",
                        }
                    ],
                    "volumes": volumes or [],
                }
            }
        }
    }


@patch("autocli.utils.run_and_wait", return_value=1)
@patch("autocli.utils.os.system", return_value=0)
@patch("autocli.utils.subprocess.run")
@patch("autocli.utils.get_deployment_spec")
def test_run_one_shot_pod_command_builds_manifest(
    mock_get_dep, mock_subproc, _mock_system, _mock_run_wait
):
    """The generated Pod manifest mirrors the deployment's container spec."""
    mock_get_dep.return_value = _fake_deployment(
        image="reg/api:1",
        env=[{"name": "DB_HOST", "value": "postgres"}],
        volume_mounts=[{"name": "code-pvc", "mountPath": "/mnt/code"}],
        volumes=[{"name": "code-pvc", "persistentVolumeClaim": {"claimName": "code"}}],
    )

    # apply succeeds; phase query returns Succeeded
    apply_result = MagicMock(returncode=0, stderr="")
    phase_result = MagicMock(stdout="Succeeded", returncode=0)
    mock_subproc.side_effect = [apply_result, phase_result]

    rc = utils.run_one_shot_pod_command(
        "api",
        command_args=["/mnt/code/api/smalls.py", "migrate"],
        action_label="migrate",
        extra_env=[{"name": "SMALLS_ENV", "value": "PROD"}],
    )

    assert rc == 0

    # First subprocess.run is `kubectl apply -f -` with the manifest on stdin
    apply_call = mock_subproc.call_args_list[0]
    assert apply_call.args[0] == "kubectl apply -f -"
    manifest_yaml = apply_call.kwargs["input"]

    manifest = yaml.safe_load(manifest_yaml)
    assert manifest["kind"] == "Pod"
    assert manifest["metadata"]["name"].startswith("api-migrate-")
    assert manifest["metadata"]["labels"]["auto.devocho/role"] == "migrate"
    spec = manifest["spec"]
    assert spec["restartPolicy"] == "Never"
    container = spec["containers"][0]
    assert container["image"] == "reg/api:1"
    assert container["command"] == ["/mnt/code/api/smalls.py", "migrate"]
    assert container["workingDir"] == "/mnt/code/api"
    # Both deployment env and extra_env are present
    env_pairs = {(e["name"], e.get("value")) for e in container["env"]}
    assert ("DB_HOST", "postgres") in env_pairs
    assert ("SMALLS_ENV", "PROD") in env_pairs
    assert container["volumeMounts"][0]["mountPath"] == "/mnt/code"
    assert spec["volumes"][0]["name"] == "code-pvc"


@patch("autocli.utils.declare_error")
@patch("autocli.utils.get_deployment_spec", return_value=None)
def test_run_one_shot_pod_command_errors_when_deployment_missing(_mock_get, mock_err):
    """Missing deployment short-circuits with a clear declare_error call."""
    rc = utils.run_one_shot_pod_command(
        "api", command_args=["x"], action_label="migrate"
    )
    assert rc == 1
    mock_err.assert_called_once()
    msg = mock_err.call_args.args[0]
    assert "Deployment 'api' not found" in msg


@patch("autocli.utils.run_and_wait", return_value=1)
@patch("autocli.utils.os.system", return_value=0)
@patch("autocli.utils.subprocess.run")
@patch("autocli.utils.get_deployment_spec")
def test_run_one_shot_pod_command_failed_phase_returns_nonzero(
    mock_get_dep, mock_subproc, _mock_system, _mock_run_wait
):
    """A non-Succeeded terminal phase returns 1."""
    mock_get_dep.return_value = _fake_deployment()
    apply_result = MagicMock(returncode=0, stderr="")
    phase_result = MagicMock(stdout="Failed", returncode=0)
    mock_subproc.side_effect = [apply_result, phase_result]

    rc = utils.run_one_shot_pod_command(
        "api", command_args=["x"], action_label="migrate"
    )
    assert rc == 1
